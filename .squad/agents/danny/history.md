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

### 2026-08-29 — "Best Options" Analyze page (design review, no implementation)

Decision: `.squad/decisions/inbox/danny-best-options-design.md` (accepted). Trigger was the user reporting far fewer CC/CSP sell alerts over two months, worse after a model swap.

**Root diagnosis of the complaint (the important finding).** The system cannot currently distinguish "no qualifying contract existed" from "the model declined to say so", because candidate quality is only ever visible through the agent's prose verdict. Three structural contributors:
- There is **no DTE filter anywhere deterministic**. `DTE <= 45` lives only in LLM prompt text and in the post-hoc `rule_evaluator._dte_cap_rule`. The agent receives every expiration.
- `filter_options_chain_by_delta` defaults are wide and **not category-aware** (calls 0.15-0.90, puts -0.60,-0.15), so candidate selection never uses the category bands the thresholds are written against. It also reads `contract.get("delta")` directly — a live violation of my own zero-free boundary decision — and it *removes* rows.
- **`iv_rank` is not observable at all.** `volatility.py` documents that yfinance has no IV history, which is why the system computes `iv_hv_ratio`/`richness` instead. Every occurrence of `iv_rank` is prompt-example text or LLM output — so `CATEGORY_THRESHOLDS_*.iv_rank_min` is being enforced against a number the model invents. An agent WAIT citing "IV Rank below category min" may correspond to nothing measurable. This is a standing correctness defect independent of the new page.

**Evaluation-approach ruling: deterministic in the critical path, LLM additive and out of band.** Reasoning worth reusing: a page whose purpose is to be *evidence against which the agent's verdict is audited* is worthless if produced by the same class of component it audits. Plus colour is a threshold-and-rank judgement — a model returns different colours on refresh for identical data, and this is a table the user compares across days. Reproducibility is the product.

**Design errors caught in review that I would otherwise have shipped:**
- **`get_or_load_async` does NOT raise on cold miss.** It does `return await self.refresh(symbol)` — full inline fetch + merge + persist, no timeout. `OptionsChainNotReadyError` is raised only by the *sync* `get_or_load` (the P1 fix I approved on 2026-08-18 applies to one path only). My planned `except OptionsChainNotReadyError -> 503` was dead code and the request would have hung. Correct fix is a new non-blocking `get_or_hydrate()`, not `asyncio.wait_for` — cancelling a refresh mid-flight while it holds the symbol lock and is writing Cosmos shards is strictly worse. Also: the roll-table endpoint's `except RuntimeError -> 503` swallows every unrelated RuntimeError, since our exception subclasses it. Do not copy that pattern.
- **Two score components monotone in the same variable.** `annualized_return` + `premium_headroom` both rise with `premium_pct` and saturate together — 0.60 of the weight on one axis, and discrimination collapses exactly at the top of the sort, the only part anyone reads. General lesson: check component *orthogonality*, not just that each component is individually defensible. Replaced with a real risk axis (`cushion` = OTM distance in units of the contract's own implied 1-sigma move over its DTE — computable from the chain alone, no price-history I/O).
- **A symbol-level term inside a per-row score is always wrong.** IV/HV richness adds an identical value to every row (zero ordering information) yet shifts every row across the colour boundaries, and when null the renormalisation lifts every score ~11%. Whole-page colours would flip on whether an HV series happened to be fetchable. Symbol-level context belongs in the parameters panel, never in the row score.
- **Category thresholds are DTE-conditional and the code has lost that.** `premium_min_pct` is written in the SKILL files as a *30-45 DTE* number. Applied flat over a 0-49 window it is unreachable for weeklies and trivial for 45-day contracts. Must scale `effective_min_pct = premium_min_pct * DTE/30`. This also means the existing `rule_evaluator._premium_floor_rule` is DTE-blind today.
- **Unknown-data must not downgrade colour.** `cosmos.get_next_earnings_date` returns `None` for any unsynced symbol (and swallows its exception), and `_is_stale` treats absent `quote_asof` as stale by design. Capping such rows at yellow would make whole symbols permanently non-green — reproducing "nothing ever fires" in a new coat of paint. Unknown -> badge, never colour.
- **Safety facts binary, economics graded.** Making the premium floor a hard gate would empty the table for aristocrats — structurally low IV is their defining property, i.e. exactly the category the user cares about. Only eligibility, delta band, and a *known* spanned earnings date are binary.

**Contract facts to reuse:** premium is always derived from **bid** (never mid), CC basis = `underlying_price`, CSP basis = `strike` — three existing call sites agree (`options_chain_filters.py:537-540`, `agent_runner.py:891-899`, SKILL.md); disagreeing on the same contract would be the worst outcome for a trust-restoring surface. Spot must come from `chain["underlying_price"]` because the Greeks were recomputed against that exact value. Put deltas are negative while CSP bands are positive — comparing raw silently empties the put table 100% of the time.

**Latent bug logged, deliberately not fixed here:** `agent_runner._CATEGORY_DELTA_RANGES` and `_resolve_category_skill` key on space-form category names while `rule_evaluator` keys on underscore form with aliases. Currently dormant because `dgi_metrics.categorize_stock` emits Title+space. Fix the normaliser only; refusing to stack a cross-agent refactor onto a new page while an alerting regression is under investigation.

**Ownership:** Linus = pure scorer + category params + DTE filter semantics. Rusty = endpoint + all frontend (Next 16, Promise params, must read `frontend/node_modules/next/dist/docs/`). Livingston = the non-blocking cache accessor **and** the real-modules integration test across the Linus/Rusty seam — assigned up front, per the 2026-08-18 lesson that unowned seams are where mutual fakes breed. Basher = adversarial cases + reviewer gate; frontend gate is lint + build only, no FE test runner exists and none is to be added.

**Named non-goal:** this page makes the alerting regression *visible*; it does not fix it. Feeding the scorer's top-N into the agents as pre-selected candidates — so a small model *ranks* instead of *searches* — is the real follow-on and needs its own decision.

### 2026-08-29 — Forced Alpha on manual CC/CSP runs (design review, no implementation)

Decision: `.squad/decisions/inbox/danny-force-alpha-design.md` (PROPOSED, two confirmations
pending). User wants manual runs of the four CC/CSP agents to guarantee an Alpha Advisor
execution; scheduled runs keep due-only behaviour.

**The finding that changes the design (H1).** `_detect_prolonged_wait`'s cooldown loop
(`agent_runner.py:1267-1274`) breaks on the first activity where `act.get("alpha_view")` is
truthy. A forced manual Alpha therefore writes an `alpha_view` onto a routine WAIT and
resets `SUPERVISOR_COOLDOWN`, so a user who forces Alpha every couple of days would
**permanently suppress** that symbol's automatic prolonged-WAIT review and its Telegram
alert. The feature meant to surface more opportunity would delete the only automatic
mechanism that surfaces it. Mitigation: persist the trigger (`alpha_run.forced`) and count
only *due* reviews in the cooldown scan; treat legacy documents with no metadata as
not-forced, which is the conservative direction (can delay a review, never suppress one).
**Reusable lesson:** whenever a gate reads "has X ever happened?" from persisted state,
adding a *second, manual* way to produce X silently rewires the gate. Check every reader of
a field before adding a new writer of it.

**Second-order safety (H2).** The Telegram prolonged-WAIT push is gated on the
`prolonged_wait` flag, not on `alpha_view` presence — so forcing is push-safe *provided*
it sets its own flag and never reuses `prolonged_wait`/`is_alert`. Ruled: forced results
are dashboard-visible only (🧠 icon, "Alpha Executed" filter, detail panel all already
exist). Reusing the existing flag would have turned one manual full-watchlist run into one
Telegram message per symbol.

**Where the rule should live.** Approved the user's behaviour (dashboard buttons always
force) but not their mechanism. Forcing lives in the API contract — `run_trigger:
"scheduled"|"manual"` + `force_alpha: bool`, manual defaults to true — not in the React
click handler. A guarantee that exists only in a click handler cannot be tested at a seam,
used from curl, or read back off a stored activity. Cost: one parameter through six files;
zero extra clicks. Rejected an enum for `force_alpha`: the only third state an enum could
add is "never", which would mean suppressing a *due* Alpha — nobody asked, and it disables
alerting. Rejected the "force only when a single symbol is named" heuristic: unpredictable
from the UI, and it would exempt the very button the user pointed at.

**Observability gap found.** `_run_alpha_review` returns `None` on every failure and the
result is persisted only when non-null, so "Alpha ran and failed" is today
indistinguishable from "Alpha never ran". A guarantee is worthless if unfalsifiable —
hence `alpha_run.status` (`ok`/`failed`/`skipped_*`) written even on failure, while
`alpha_view` keeps its current present-only-on-success semantics so no existing reader
changes. Same reasoning applied to the two deliberate skips (`buy_tracker`'s
`_skip_reviews`, and `incomplete_quote_wait` in the position monitor, which forcing must
**not** override — Alpha on sanitized no-quote prose would invent a recommendation).

**Pre-existing defect pulled into scope.** `POST /api/trigger/{agent_type}` has no
in-flight guard at all: two clicks = two concurrent full sweeps, duplicate writers on the
same activity stream. `TriggerButton`'s disabled state resets when the fire-and-forget POST
returns, so it never guarded anything. Latent waste today; with forced Alpha it doubles a
real bill, so the guard ships with this change (409 + in-flight registry mirroring the
existing `_full_analysis_status` pattern, reusing `_MAX_TASK_DURATION_SECONDS` for stale
reclamation rather than inventing a second timeout).

**Semantic split escalated, not swallowed.** Two manual-looking buttons will behave
differently: the dashboard buttons force, while Settings' "Run Now" goes through
`TaskRegistry`, whose queue carries only a task name and whose jobs are pre-bound
zero-arg callables — forcing there means changing shared scheduling machinery used by ten
tasks for one flag. Proposed keeping it scheduled-semantics, but flagged for user
confirmation rather than silently accepting it; likewise whether `/api/trigger-all`
(the biggest cost delta) forces.

**Documentation was already ahead of the code:** `docs/concepts.md:253` lists Alpha's
triggers as "alerts, prolonged WAITs, on-demand" — on-demand has never existed. This work
makes the doc true rather than adding a new concept.

**Ownership:** Linus = runner gate + pass-throughs + cooldown neutrality + cron
`force_alpha=False` lock. Rusty = endpoint contract, concurrency guard, and the 409 UI
state. Livingston = the real-modules API↔runner seam test, assigned up front. Basher =
adversarial + gate, with H1 as the named must-not-regress. Scribe = `docs/concepts.md`.

### 2026-08-29 (later) — Best Options row-inclusion: superseded §4.1/§4.2 wording, ratified as a durable record

Basher rejected Best Options (`basher-best-options-review.md`, Defect 1): the ACCEPTED
design's own text conflicted with the shipped, product-owner-corrected behaviour, and
`.squad/decisions.md` had zero entries reconciling the two. Linus is locked out for this
revision cycle (original author of both the evaluator and the account of the correction);
this was mine to own as design owner, documentation/design only, no evaluator or frontend
code touched.

**What actually happened, reconstructed from Linus's own decision draft and the
`best_options.py` docstring (both already correct — the gap was purely in the design
document I own).** My original §4.1 said the DTE window is the *only* row-inclusion
filter ("nothing inside the window is ever hidden") and §4.2 filed the delta band as
hard-gate "G2" — binary, but "failure = red, row still shown," i.e. colour-only. Linus's
first implementation followed that literal text. It was wrong against my own problem
statement (§1): the ask was a table of "every option ... within the near-dated window
**and the configured delta ranges**" — delta was always meant as a second inclusion
filter, not a colour input. The product owner confirmed this explicitly after reviewing
Linus's first pass, and the evaluator was corrected the same day: a side's `rows` now
require both the DTE window and the category's `abs(delta)` band; excluded contracts
never appear in `rows`, only in `nearest_miss` and the new `excluded_by_delta_band` count.
I never amended the design to match — that's the defect.

**Root-cause note worth keeping.** This is the second time this exact failure mode has
hit Best Options in one day (see the F2/"G2" framing above): a hard gate described only in
terms of its *colour* effect reads, to an implementer working fast, as if colour is its
*only* effect. When a requirement is "X must be true for a row to appear at all," it needs
to be written as a row-inclusion filter in the same section that defines row inclusion
(§4.1), never left to be inferred from a colour-gate table in a different section (§4.2).
I've restructured both sections so the filter/gate distinction is stated as the section
heading, not just implied by prose.

**Disposition.** Amended `danny-best-options-design.md` in place: new §2A states the
corrected rule up front and names why the original wording was wrong; §4.1 and §4.2 carry
the corrected normative text, with the original wording preserved as an explicitly marked,
non-normative historical note (not deleted — the record of what changed and why matters as
much as the correction itself). Cross-references in §4.6, §5, and the §7 payload example
(`excluded_by_delta_band` was missing from the response-shape example entirely) updated to
match. Also formally ratified the correction as a standalone durable record —
`.squad/decisions/inbox/danny-best-options-delta-filter-correction.md` — since Basher
correctly noted no citable entry existed anywhere for this decision; that record, not
prose buried in a design doc's amendment section, is what a future `decisions.md` entry or
cold reader should cite.

**Scope discipline.** Did not touch `backend/src/best_options.py`, any test file, or any
frontend file. Basher's Defect 2 (the frontend `parameters` contract mismatch in
`frontend/src/types/best-options.ts` / `BestOptionsParams.tsx`) is untouched and remains
open — outside this correction's scope (Livingston/Rusty's surface per Basher's own
recommended ownership).

**Re-review target for Basher:** `.squad/decisions/inbox/danny-best-options-design.md`
(§2A and amended §4.1/§4.2) and
`.squad/decisions/inbox/danny-best-options-delta-filter-correction.md`. Defect 1 only —
Defect 2 stands as rejected until its own owner revises it.
