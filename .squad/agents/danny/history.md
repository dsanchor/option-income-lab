# Danny — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Stack:** Python, Microsoft Agent Framework, Azure Foundry (gpt-5.1)
- **MCP:** iflow-mcp_ferdousbhai_investor-agent 1.6.3
- **Description:** Two periodic trading agents for covered call and cash-secured put sell signals. Local runtime, configurable polling, stock symbols from files, decision logs, sell signal alerts.

## Core Context

### 2026-08-08 — Revisión watchlist: alta de símbolos y filtros de señal (rama uinext)

**Archivos inspeccionados:** `SymbolsTable.tsx`, `types/symbols.ts`, `symbols/page.tsx`, `SymbolActions.tsx`, BFF routes `/api/symbols` y `/api/symbols/[symbol]`, `backend/web/app.py`, template legacy `symbols.html` (git history).

**Hallazgos:**
- Alta de símbolos se perdió al eliminar templates en commit `7637787`. BFF (`POST /api/symbols`) y backend OK; solo falta UI (`AddSymbolForm` client component).
- Filtros Ideal Calls/Puts (pills) existían en legacy como client-side JS. No portados a `SymbolsTable.tsx`. Todos los campos necesarios (`entry_tag`, `momentum`) ya están en `SymbolRow`.
- Lógica exacta de filtros documentada en `.squad/decisions/inbox/danny-watchlist-review.md`.
- `total_shares` no es editable desde la tabla ni desde el formulario de alta en el nuevo frontend.
- Contrato PUT `/api/symbols/{symbol}` es partial update; BFF ya implementado en `[symbol]/route.ts`.
- Riesgo principal: 409 fallback (símbolo ya existente → activar watchlist) no implementado.

**Patrones arquitectónicos observados:**
- `symbols/page.tsx` es Server Component; formularios interactivos deben importarse como client components separados.
- BFF routes son proxies puros al Python backend, sin lógica propia.
- `SymbolActions.tsx` maneja los toggles CC/CSP/Buy en la detail page; la tabla no tiene acciones inline propias.

### DGI Screener & Timing Architecture (2026-05 to 2026-06)
- **Scope:** Top 20 DGI candidates with technical timing indicators (RSI, SMA, Bollinger Bands) + manual "Quick Analysis" / "Add to Watchlist" buttons
- **Infrastructure:** Daily scheduler, yfinance data source, CosmosDB storage, web dashboard UI, position snapshots container (180d TTL)
- **Design principle:** Screener is "opinionated" about timing; technical indicators are always programmatic (no LLM); LLM used only in contextual analysis
- **User directive:** David wants entry-point timing, not generic stock lists. Quality score: 70% fundamental + 30% technical.

### Contrarian Agent Architecture (2026-07)
- **Decision mapping:** 4 agent types × valid decisions = 16 decision-specific playbooks with parameterized instructions
- **Anti-noise rules:** WEAK self-assessment for solid decisions; forbid arguments against risk management
- **Validation:** Agent output schema rejects invalid combinations (e.g., open_put + ROLL_UP)
- **Guardrails:** Hard 45 DTE cap, near-ATM stability buffer (3% zone), ROLL_OUT restricted to near-ATM ≤5 DTE

### Scheduler & DPS Integration (2026-06)
- **Architecture:** TaskRegistry singleton at `src/scheduler_registry.py` (185 lines) manages all 8 scheduled tasks uniformly
- **DPS integration:** Real-time scoring (4x/day via monitoring agents) replaces nightly batch job; no impact on instruction logic
- **Code impact:** `src/main.py` reduced 1266 → 736 lines (41% reduction); web UI duplication reduced 150+ lines
- **Persistence:** Last-run timestamps persisted to CosmosDB, survive scheduler restarts; all tasks uniformly expose enabled checkbox + run_now button

### Telegram & Settings Infrastructure (2026-03 to 2026-06)
- CosmosDB Settings container with deep-merge behavior, initialization on first run, configuration API reference
- Telegram notification system for decision/alert delivery; BotFather setup, env var/config.yaml integration
- Real-time settings UI with enable/disable toggles and per-task run_now buttons


## Learnings

### README Modularization (2026-07-15)

- The project adopted a **docs/ structure** with the README as a lightweight index/overview (~135 lines, down from 1720)
- **Documentation split**: Content moved VERBATIM into 11 topic-specific files:
  - `docs/concepts.md` — Core system concepts (Activity vs Alert, DPS, Supervisor, Alpha Advisor, Position Lifecycle)
  - `docs/architecture.md` — System design, agent pipeline, data flow, pre-fetch architecture, CosmosDB model, project structure
  - `docs/chat.md` — Dual-mode chat (Portfolio Chat, Quick Analysis, Per-Activity Chat)
  - `docs/screener.md` — DGI Screener, Momentum Analysis, Buy Tracker, Category-Based Strategy Skills
  - `docs/agents.md` — Summarization Agent, Symbol Report, Action Plans
  - `docs/output.md` — Activity & alert documents, example JSON, Telegram notifications
  - `docs/web-dashboard.md` — Dashboard UI, pages, features
  - `docs/local-setup.md` — Local setup, prerequisites, Python venv, Docker
  - `docs/deployment.md` — Azure deployment, CosmosDB provisioning, environment variables
  - `docs/troubleshooting.md` — Common errors and fixes
  - `docs/development.md` — Skills architecture, instruction files, SDK information
- **README convention**: README must remain a lightweight index with:
  - Philosophy (the "why")
  - Architecture overview (1 paragraph + bullet list of agents)
  - Documentation table (links to all docs/*.md files)
  - Features highlights (2-3 lines per feature with links to details)
  - Quick Start (minimal commands to get running)
  - Acknowledgments
- **Breadcrumbs**: Every `docs/*.md` file starts with `[← Back to README](../README.md)` immediately under the H1 title
- **Maintenance**: When features change, update BOTH the relevant `docs/*.md` file AND the corresponding highlight in the README's Features section
- All code blocks, tables, JSON examples, and CLI snippets were moved VERBATIM — no content was lost or summarized

### Roll Table Feature — Architecture Consultation (2026-07-23)

- **Activity detail view**: `web/app.py:3096` (`GET /activities/{activity_id}`) → template `web/templates/activity_detail.html`. Handler fetches activity from CosmosDB, passes: `activity`, `symbol`, `display_name`, `agent_label`, `agent_type`, `is_alert`. No chain data today.
- **Options chain cache**: `src/options_chain_cache.py` — singleton, 30-min TTL, yfinance (ALL expirations) + TradingView overlay. Key format: `calls[YYYYMMDD][strike_str]` and `puts[...]`. `get_or_load_async` is the correct entry point for async endpoints.
- **Existing roll math**: `src/options_chain_filters.py:403` — `format_roll_candidates_table()` already computes buyback_cost, net_credit, premium_pct, ann_ret for each candidate. Returns plain-text markdown, not structured JSON. `get_contract()` and `exclude_contract()` helpers also exist.
- **Design decision**: New endpoint `GET /api/activities/{activity_id}/roll-table` (pure JSON). Uses cache, NOT `provider.fetch_all`. New module `src/roll_table.py` with `compute_roll_table()` returning structured dict for 4 expiries × 3 strikes (ATM, +3%, -3% relative to underlying price).
- **Template**: Add JS async fetch + green/red table to `activity_detail.html`, visible only for `open_call_monitor` / `open_put_monitor` agent types.
- **Scope boundary**: Linus owns `compute_roll_table()` math; Rusty owns endpoint + template rendering.
- **Critical**: Debug endpoint (`/api/debug/agent-chain/{symbol}`) uses `provider.fetch_all` directly (bypasses cache). Roll table endpoint must use cache for latency + consistency.

### 2026-08-08 — Watchlist Review Resolution
- Rusty restored symbol creation and inline `total_shares` editing; successful creation triggers forecast backfill without coupling backfill failure to persistence.
- Linus restored the documented suitability categories from `entry_tag` plus momentum. These categories are independent of watchlist tracking flags and option-chain delta filters.
- Basher's final current-state review approved the integrated implementation; the earlier missing-feature findings and inbox review are superseded.

### 2026-08-17 — Buy Tracker Normalization Test Revision
- Replaced fabricated provider fields with the actual yfinance output shape: indicator `signal` confirmations plus annual/latest/growth dividend evidence.
- Added provider-shaped `STRONG_BUY` reachability and fail-closed missing dividend-state regressions, the negative payout `<=75%` boundary, and canonical hard-WAIT prompt-example checks.
- Complete focused test files: 181 passed (one existing pandas-ta deprecation warning).

### 2026-08-17 — Buy Tracker Provider-Proxy Contract Resolution
- Amended the accepted contract so `MACD.macd` and `Stoch.K` Buy signals plus positive annual DPS, latest DPS, and dividend-growth years replace unavailable original confirmation fields.
- Confirmed a missing explicit cut boolean does not block `STRONG_BUY`; an explicit cut or the exact canonical cut flag still forces `WAIT`, and every accepted proxy remains fail-closed.
- Audited implementation and shared prompts; current behavior already matches the directive. Focused suites: 200 passed (one existing pandas-ta warning).

### 2026-08-18 — Persistent Option Chain Merge (design review)

- **Invariant is half-implemented.** `OptionsChainCache` already does field-level last-known-good merge, already refuses TTL eviction, already prunes by real expiration. The blocker is that "persisted" chain = `self._store`, a process-local dict — restart wipes it, and web vs. scheduler processes hold *separate* singletons that never converge.
- **TradingView silently destroys valid Yahoo fields.** `_merge_contract_fields` starts from `dict(new)`; TV hardcodes `volume:0`, `openInterest:0`, `lastTradeDate:None`, `inTheMoney:False`, `contractSymbol:""`, none of which are in `_QUOTE_FIELDS`. Root cause is fabricated zeros for unobservable fields, not the merge itself.
- **Key rule learned: absence ≠ zero.** Providers must emit `None` for what they cannot observe; the merger treats missing/`None` as "no opinion". This fixes the TV overlay without weakening the correct rule that a Yahoo-*observed* `volume: 0` overwrites a prior 500.
- **Zero is field-dependent, not global.** `bid == 0` is a *real* market state (`options_math.robust_mid` documents it explicitly); `ask == 0` and `iv == 0` never are. Discriminator adopted: a contract's quote group is trusted only if that payload supplies a valid `ask` or `iv`; a `(source, side, expiration)` bucket of ≥3 contracts all failing that gate is discarded wholesale (the "Yahoo all-zero chain" mode).
- **Derived fields must never be merged.** `mid` + the 5 greeks are outputs of `robust_mid`/`GreeksCalculator`. Merging them independently yields `delta` from cycle N-3 alongside `iv` from cycle N — and `filter_options_chain_by_delta` gates candidate selection on delta. Recompute post-merge; also makes carried-forward contracts decay theta/DTE correctly.
- **Retention needs provenance.** Indefinite retention without `_meta.quote_asof` just trades a zero-data bug for a silent stale-data bug. Schema-doc update for the LLM is mandatory, not optional.
- **Concurrency hole:** `refresh()` loads the prior chain at step 4 and writes at step 6 with the lock released between — `refresh_all` + SWR + `/api/trigger` overlap silently loses contracts. Fix: per-symbol lock over the whole hydrate→merge→persist sequence; `merge_prior` kept monotone so Cosmos ETag CAS-retry is safe cross-process.
- **Cosmos sharding is mandatory:** one doc per `(symbol, expiration)` (`optchain_{SYM}_{YYYYMMDD}`); a whole-chain doc plausibly exceeds the 2 MB item limit for liquid names. Pruning = delete shard after a 7-day post-expiry grace.
- **Leak found:** TV's `expiration = str(raw_exp)` fallback produces non-`YYYYMMDD` keys that `_prune_expired_expirations` deliberately skips — immortal under never-evict semantics. Reject unparseable expirations at ingestion.
- **Ownership split:** Linus = `src/options_chain_merge.py` pure semantics + source normalizers (no Cosmos/threading); Rusty = `src/options_chain_store.py` + cache lifecycle/concurrency/config/schema-doc (no validity rules). Seven-function interface frozen up front so both work in parallel.
- **Guardrail:** `refresh_all`'s per-symbol timeout and `shutdown(wait=False, cancel_futures=True)` are untouchable (2026-06-30 watchdog decision) — explicit regression test assigned.
- **Revision path chosen: escalate, don't reassign.** With Linus/Rusty locked out, Basher reviewer-only and Lead non-implementing, there was no eligible in-house owner — and the defects sit in the *seam between* the two locked-out charters, which is exactly why neither owned it. Cast Livingston as Persistence & Integration Engineer (Cosmos round-trip fidelity, asyncio-vs-threading, cross-module integration tests). Directive: `.squad/decisions/inbox/danny-revision-directive-option-chain-2026-08-18.md`.
- **Scope discipline for the redo:** `options_chain_merge.py` + its tests + provider normalizers + the LLM schema doc are byte-frozen (accepted and correct) — fix the callers. Only the store, the cache's hydration/locking path, their tests, and one new real-modules integration file may change. D4 explicitly supersedes the design's literal `threading.RLock` wording: the accepted artifact is the *invariant* (one refresh per symbol, no lost update, loop never blocked), not the primitive.
- **Structural lesson to carry forward:** when an ownership split freezes an interface, assign the *integration test across that interface* to a named owner up front. Unowned seams are where mutual fakes breed.

### 2026-08-18 — Persistent Option Chain (Livingston revision) — APPROVED, with one P1 follow-up

- **D1–D5 all independently re-reproduced as fixed.** 3 real persist cycles → hydrated contracts keep `mid`+5 greeks and produce an identical `filter_options_chain_by_delta` set to the producer's memory; carried TV contract hydrates `_meta` byte-identical (`quote_source: tradingview`, `quote_asof`/`first_seen` unmoved, `carried: true`); hydrate prunes a 3-day-expired bucket while the shard stays inside the 7-day grace, restores `symbol`/`timestamp`/`underlying_price`, is immediately stale-eligible and provably triggers a real background fetch; two same-loop `await refresh(sym)` → exactly 1 fetch; 60/60 heartbeat ticks while waiting on a cross-thread lock hold; identical market data → `unchanged`, zero rewrite. Frozen files verified untouched (merge module 626 lines, all 7 functions at identical offsets; tracked frozen diffs byte-identical to pre-revision `--stat`); watchdog intact. 615 focused tests pass.
- **Key architectural insight from the fix:** `merge_prior` is "apply live observations to prior", so calling it on two *already-merged* chains was a category error — the store needed a different operation entirely (verbatim contract-level union by `_meta` recency). Lesson: a frozen interface must be documented with its *input category*, not just its signature; D1/D2 were a caller misuse that a type-level distinction (live-observation chain vs accumulated chain) would have made unrepresentable.
- **Accepted tension, resolved in the safe direction:** D5's write-skip means an unchanged shard is not rewritten, so persisted `quote_asof`/`last_seen` can lag memory when values are identical. Provenance can therefore *understate* freshness but never overstate it — the dangerous direction (D2's original failure) is closed. Documented rather than "fixed", since fixing it would re-break D5.
- **P1 follow-up found during the gate (not a D1–D5 defect):** the per-symbol OS lock turns `get_or_load`'s pre-existing sync-in-async bridge into a deadlock — an in-flight same-loop `refresh(S)` holds the lock, the loop then blocks in `.result(timeout=120)`, and the pool worker waits for a lock only the blocked loop can release. Measured: control 2.3s, contended >40s (no completion). Single reachable call site, `web/app.py:3249` inside `async def api_activity_chat`; window widens whenever hydrate returns `None` (persistence disabled/Cosmos down). Fix belongs in the cache (bounded acquire with last-known-good fallback, or route the running-loop branch through the same in-flight task).
- **Process note:** escalating to a fresh specialist rather than reassigning worked — the fix landed in the seam neither original charter owned, and required *zero* edits to `test_options_chain_cache.py`, which is the strongest available evidence that no accepted behaviour was traded away.

### 2026-08-18 — P1 sync-bridge deadlock (Livingston) — APPROVED, feature cleared

- **Fix verified by reproduction, not by reading:** the previously-deadlocking case (in-flight same-loop `refresh(S)` + sync `get_or_load(S)` cold miss, measured >40s no-completion before) now returns control in **0.000s** and the in-flight refresh finishes normally. Cold miss from a running loop raises `OptionsChainNotReadyError`, schedules a background refresh that lands (1 fetch), and the next call serves the cache; with a refresh already in flight the try-acquire suppresses a duplicate (total fetches = 1). Genuine sync callers (no running loop) keep the full blocking refresh (0.40s, valid JSON); cached and hydrated hits never raise; `refresh_all` unaffected (2/2 success).
- **Fail-fast beats fail-slow when the caller already degrades gracefully.** The only reachable call site (`web/app.py:3249`, `async def api_activity_chat`) wraps the call in `except Exception` with an "(option chain unavailable: …)" fallback, so raising is strictly better than a 120s stall — and `OptionsChainNotReadyError` subclassing `RuntimeError` kept it compatible without opening the frozen file. Worth remembering: when a frozen caller already has a broad handler, a *narrower typed exception under an existing base class* is the cheapest safe seam.
- **Frozen surfaces proven by fingerprint, not assertion:** `options_chain_merge.py` and its test file md5s are bit-identical to the values recorded at the previous gate; store/store-tests/integration-tests untouched; tracked frozen diffs unchanged; watchdog (`_REFRESH_SYMBOL_TIMEOUT=90`, `shutdown(wait=False, cancel_futures=True)`) and `invalidate`/`purge` intact; all 14 pre-existing cache test classes still present; 620 focused tests pass (+5, none removed).
- **Closing judgement on the whole arc:** the directive's original invariant — never let a bad or missing quote erase a good one — is now enforced end-to-end (in-memory merge, Cosmos round trip, cold-replica serving) with provenance that can only understate freshness. Three review rounds, one lockout, one fresh specialist; the decisive move was refusing to accept green tests as evidence when the fakes met each other at the seam.
