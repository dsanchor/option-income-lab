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

### 2026-08-29 (later still) — Best Options: 45d DTE alignment + `coverable_contracts` removal (design review, no implementation)

Decision: `.squad/decisions/inbox/danny-best-options-45d-design.md` (accepted). User
directive: align Best Options' DTE window to the agents' 45-day cap and remove the
coverable-contract calculation/field entirely.

**Boundary was verified, not assumed.** The agents' cap is `DTE <= 45` — inclusive, window
`[0, 45]` — confirmed identically across `rule_evaluator._dte_cap_rule`
(`status = STATUS_PASS if dte <= 45 else STATUS_FAIL`), every CC/CSP instruction file, the
earnings-gate skill, and the supervisor instructions. `best_options.py` already carries this
exact number as `SYSTEM_DTE_CAP = 45`; the bug was that the page's *default window*
(`DEFAULT_DTE_MAX = 49`) never actually equaled the cap it claims to mirror — a four-day
silent divergence between "the page that audits the agents" and "the agents."

**Key design call: alignment is about the default, not about removing the override.** The
endpoint's explicit `dte_max` query override (up to `le=60`) stays untouched, and so does the
`exceeds_system_dte_cap` flag — both become unreachable under an unmodified request once the
default equals 45, but remain live and meaningful the instant a caller explicitly widens the
window. Removing the override entirely would have quietly narrowed the page's own stated
purpose (being *evidence* for the alerting-regression complaint) under cover of an unrelated
directive. Named, not assumed.

**`no_shares_held` kept; `coverable_contracts` did not survive the semantics test.** The two
looked coupled (`no_shares_held = coverable_contracts == 0`) but only one carries information
no other field can reconstruct without it: `no_shares_held` is a distinct disclosure
("can't cover even one contract") read by exactly one UI element (the page's banner) and
computable directly from `total_shares` without exposing the derived count. Ruling: recompute
it inline (`max(total_shares, 0) < 100`), delete the `coverable` intermediate and the
`coverable_contracts` key entirely — not defaulted to `null`, not renamed, gone.

**Load-bearing test coupling found during the surface sweep, named up front so it isn't
discovered the hard way during implementation:** `test_best_options_endpoint.py` has an
endpoint-vs-direct-call parity test that calls the live endpoint with no query overrides (so
it exercises whatever the *default* is) and diffs the result against a direct
`evaluate_best_options(..., dte_max=49, ...)` call. Changing the endpoint's default to 45
without also updating that literal turns the test into a silent no-op that compares two
different windows and could still report green. Same category of coupling in
`test_best_options_adversarial.py`: the `TestDteWindowBoundaries` suite's own boundary values
(49/50) must move to (45/46), and its `exceeds_system_dte_cap` tests must gain an explicit
`dte_max=60` override since a bare DTE-46 contract no longer merely gets flagged under the
new default — it's excluded from the window before the flag logic ever runs. The test file's
separate `_evaluate()` helper default (also hardcoded `49`, independent of production's
constant) needs a deliberate, documented decision either way rather than being left to
silently disagree with the new production default.

**Ownership, matching this feature's established split:** Linus = `best_options.py` +
`test_best_options.py`. Rusty = endpoint (`web/app.py`) + both frontend files
(`types/best-options.ts`, `BestOptionsView.tsx`) + `test_best_options_endpoint.py`.
Livingston = `test_best_options_frontend_contract.py` (the cross-seam contract test, his
standing role on this feature). Basher = `test_best_options_adversarial.py` + reviewer gate,
with an explicit named must-not-regress list: zero remaining `coverable_contracts`
occurrences (grep, not just green tests), `no_shares_held` semantics unchanged, the
endpoint/direct-call parity test actually exercising the new default, and
`exceeds_system_dte_cap` proven reachable under an explicit override (not orphaned).

**Named non-surface, checked and confirmed empty:** `docs/*.md` has zero references to Best
Options' DTE window or `coverable_contracts` — no documentation debt from this change.
`test_options_chain_dte_filter.py` tests the generic, reusable DTE-filter primitive with its
own arbitrary test values and is explicitly out of scope — flagged so a future implementer
doesn't mistake it for a Best-Options-specific surface and touch it unnecessarily.

### 2026-08-29 (later still) — Best Options: remove user-facing architecture/process commentary (design review, no implementation)

Decision: `.squad/decisions/inbox/danny-best-options-copy-removal-design.md` (accepted).
User directive: strip determinism/no-LLM/agent-comparison commentary from the page's own
copy, keep the page itself purely informational.

**Full sweep, not a spot-fix.** Grepped `frontend/src`, `backend/src`, `backend/web` for the
directive's named phrases and equivalents. Found four true user-facing hits: the design's
own original §6 "standing caption" in `BestOptionsParams.tsx` (the exact target text,
verbatim); the page H1 subtitle in `BestOptionsView.tsx` ("... deterministic, no LLM in this
path" — only the trailing clause is commentary, the factual "DTE window and delta bands"
description in front of the dash stays); and `best_options.py`'s `iv_rank_note` field, whose
final sentence ("The agent path evaluates it against a model-supplied value...") is the same
class of agent-internals commentary even though the field's first two sentences are
legitimate, actionable product information and must stay. Two more hits (a JSDoc comment and
a type-file header comment) are developer-facing source documentation, never rendered — out
of scope, confirmed rather than assumed, since "user-facing" is the directive's own boundary.

**Judgment call, written down so it isn't re-litigated:** `thresholds_source` /
`skill_reference` (backend source file paths shown in the details panel) look adjacent to
"architecture commentary" but are not — they are the parameter-provenance disclosure the
original design made *mandatory*, independently protected by its own standing reviewer-gate
rule in `.squad/decisions.md` ("a permanent addition to the Best Options reviewer gate").
Same reasoning kept the `exceeds_system_dte_cap` flag's "Beyond agents' 45d cap" label and
the DTE field's "(agent cap {system_cap}d)" annotation — both are data-driven facts tied to
the just-ratified 45d-alignment decision, not commentary about how any system decides.
Removing either under this directive's cover would have quietly clawed back a different,
still-binding decision.

**Test surface is small and precisely bounded.** Exactly one existing assertion touches the
affected string (`test_best_options_adversarial.py:852`, substring `"not enforced"`), and it
survives the trim unchanged — verified by reading the assertion, not by assuming the trim was
safe. Added one negative assertion so the removed clause can't silently reappear.

**Ownership:** Linus = the one backend string (`best_options.py`'s `iv_rank_note`). Rusty =
both frontend components (`BestOptionsParams.tsx`, `BestOptionsView.tsx`). Basher = the new
test assertion plus a grep-based reviewer gate — confirming absence of the four phrases *and*
presence of the deliberately-kept disclosures, so the gate can't be satisfied by an
over-broad strip that also deletes legitimate substance.

### 2026-08-29 (later still) — Supervisor/Alpha execution traces: focused design review (no implementation)

Decision: `.squad/decisions/inbox/danny-supervisor-alpha-traces-design.md` (accepted).
Trigger: `.squad/decisions/inbox/copilot-supervisor-alpha-traces.md` — user directive to
persist complete, separate execution traces for Supervisor and Alpha, not just their parsed
`supervisor_view`/`alpha_view` fields on the activity document.

**The gap, confirmed by reading the code, not inferred from the trigger's own framing.**
`_record_trace` is called at exactly 4 sites (`analysis`, `assessment`, `roll`,
`plan_monitor`) — all primary-decision phases. `_run_supervisor_review` and
`_run_alpha_review` never call it at all; both wrap their entire body in one broad
`try/except Exception: logger.warning(...); return None`, so a raised exception or an
unparseable/invalid response leaves zero trace of the raw prompt, raw response, or failure
reason anywhere in CosmosDB — only a console log line. 11 call sites (7 supervisor, 4
alpha) invoke these two methods across `run_symbol_agent` and `run_position_monitor`.

**Key design call: trace-recording has to move *inside* `_run_supervisor_review`/
`_run_alpha_review`, in a `try/finally`, not stay at the call site.** The call site only
ever sees the already-swallowed `None` return — it has no access to the prompt that was
built, the raw response (if any), or the specific reason a `None` came back. Moving the
`_record_trace(...)` call into a `finally` block (with `instructions`/`message`/
`response_text`/`error`/`supervisor_data` all initialized to `None` *before* the `try:`, so
the `finally` can safely reference them even on a pre-`agent.run()` crash) is the only shape
that satisfies "record prompt/raw response/errors including parse failures and exceptions."
Every existing early `return None` branch inside the parsing logic must also set a specific
`error` string first (`"no_parseable_json"`, `"missing_required_fields:{...}"`,
`"invalid_challenge_strength:{...}"`/`"invalid_opportunity_strength:{...}"`, or the
exception's `f"{type}: {msg}"`) — today those branches silently return `None` with nothing
but a log line explaining why.

**Correlation model, two fields, deliberately not one.** `run_id` — a fresh `uuid4()`
minted once at the top of each per-symbol decision function, before any phase runs — groups
every trace belonging to one decision cycle. I rejected reusing the activity document's own
`id` as the correlator: it's only known after `write_activity` succeeds, which would couple
the trace layer's ability to correlate to the data layer's write succeeding, and it fights
the trigger's own "separate trace records" framing. `parent_trace_id` is a flat, one-hop
pointer to the actual trace-document id of the phase that causally precedes it (not the
run's root generically) — for Supervisor/Alpha specifically, that's the `roll` phase's trace
id when a roll happened this cycle, else `assessment`/`analysis`'s, because Supervisor/Alpha
audit the decision that was actually made, and when a roll occurred that decision is the
roll's. Mechanically this means `_record_trace` must stop being a void function — it now
returns the trace `id` it generated (or `None` if disabled/failed, so a suppressed trace can
never produce a dangling parent reference), and `_run_position_assessment` /
`_run_roll_management` each gain one new tuple-return element so their trace ids can be
threaded downstream.

**`agent_type` naming ruling — reuse the primary agent_type, never invent a new one.**
Confirmed by reading `_run_supervisor_review`/`_run_alpha_review`: `_AGENT_TYPE_MAP` remaps
`open_call_monitor`/`open_put_monitor` to `open_call`/`open_put` *only* to pick the right
instructions file — that remapped variable must never leak into the trace's `agent_type`
field. Keeping the original, unmapped `agent_type` means the existing `enabled_types`
per-agent-type capture toggle (Settings → Agent Logs) governs a whole pipeline's tracing —
primary phase and its reviews together — with zero new settings surface. `phase` gains two
new literal values (`"supervisor"`, `"alpha"`) — there's no central enum anywhere in this
codebase for phase names, so this needed no schema change. Confirmed directly in both
`AgentLogsView.tsx` and `[trace_id]/page.tsx` that neither hardcodes a phase allowlist —
Supervisor/Alpha rows render with **zero required frontend code changes**.

**Relation to `alpha_run`, and why I added a new field instead of extending it.**
`alpha_run.status` on the activity document already says *whether* Alpha ran/was
forced/failed — it has never said *why* a failure failed. Rather than denormalizing the new
trace's `error` text into `alpha_run` (which would duplicate observability detail into the
hot-path activity document and create two disagreeing sources of truth), I'm adding one new,
same-named join key — `run_id` — directly onto the activity document, in both success and
error write paths, so any activity (not just failed ones) can be cross-referenced to its
full trace set with one field.

**Explicitly ruled: no trace for skipped reviews.** A trace's entire purpose is to record
what a model call did; the three existing skip paths (`buy_tracker`, calm-WAIT non-forced,
`incomplete_quote_wait`) never call a model at all. I rejected writing a synthetic "skipped"
trace doc for table-parity — it would populate `model`/`duration_seconds`/`response_text`
with meaningless nulls and create a second, disagreeing home for a fact (`alpha_run.status`)
that already has exactly one authoritative home today.

**Named but explicitly out of scope, so it isn't silently rediscovered later.** (1) Model
completeness: `model` is often `None` at these call sites even though `_get_client` silently
resolves a real deployment (`model or self._default_model`) — I'm requiring the fix
(`resolved_model`) for the two *new* Supervisor/Alpha sites since it's directly the field
this design is asked to complete, but the same gap exists at the 4 pre-existing
`_record_trace` sites and I'm leaving those alone, named as a trivial recommended follow-up.
(2) `backend/scripts/provision_cosmosdb.sh`'s custom indexing policy (meant to exclude large
blob paths from indexing) is applied to the wrong container — hardcoded `$CONTAINER_NAME`
(`"symbols"`), never reassigned to `$TRACES_CONTAINER` — so `agent_traces` runs under
Cosmos's default index-everything policy today. Pre-existing, affects every trace phase
equally, not introduced or worsened by this design — flagged, not fixed, here.

**Reused verbatim, confirmed unnecessary to change:** the 90-day `AGENT_TRACE_TTL_SECONDS`
TTL (inherited automatically — Supervisor/Alpha traces go through the same
`write_agent_trace` into the same container), and the `agent_traces` container's partition
key / lack of any DDL change (Cosmos is schemaless; `run_id`/`parent_trace_id` are just two
new optional document properties). Trace volume roughly doubles-to-triples per decision
cycle when Supervisor/Alpha run — the two already-existing levers (per-agent-type capture
toggle, manual purge endpoint) are sufficient; no new retention/size mechanism is needed.

**Ownership, matching this codebase's established split:** Rusty owns
`backend/src/agent_runner.py` (the runner-logic restructuring) plus the frontend TS types.
Livingston owns `backend/src/cosmos_db.py` (his "CosmosDB document round-trips: schema
fidelity" remit exactly) plus its own cross-seam round-trip test. Basher owns a new
adversarial test file plus the reviewer gate, with a named must-not-regress list: all 11
call sites thread `cosmos`/`run_id`/`parent_trace_id` (grep, not test-count alone), zero
trace writes for the three skip paths, the model-completeness fix actually resolves `None`,
and the four existing tests that already monkeypatch `_record_trace`
(`test_force_alpha_execution.py`, `test_open_call_zero_quote.py`,
`test_buy_tracker_normalization.py`, `test_zero_free_agent_chain.py`) stay green unmodified.
Linus is not involved — no strategy/instructions-file surface is touched by this design.

### 2026-08-29 (later still) — Best Options scheduled precompute + shared in-memory cache: focused design review (no implementation)

Decision: `.squad/decisions/inbox/danny-best-options-scheduler-design.md` (accepted).
Three directives folded into one design: `copilot-best-options-scheduled-memory-cache.md`
(precompute per symbol, share one cached result across Symbol Detail and the Options
Screener, Settings config, Mon-Fri hourly at :05 from 10:05 to 23:05),
`copilot-best-options-symbol-refresh-only.md` (a per-symbol Refresh button on Symbol Detail
only, never on the Screener), and `copilot-options-screener-precomputed-only.md` (the
Screener never computes on request; it shows `N of X loaded`).

**Timezone was determined from the code, not from the config comments — and the config was
found to be lying.** `scheduler.timezone: "UTC"` in `config.yaml` is read by *nothing*: the
only other occurrence of that key in the repo is `Config.timezone`, which does not read
config at all but *reports* `datetime.now().astimezone().tzinfo`. Every cron in this system
is evaluated by `croniter(expr, _now_local())` where `_now_local()` is
`datetime.now().astimezone()` — the container's system timezone, full stop. The "(UTC)"
comments scattered through `config.yaml` are assertions about the deployment's `TZ`, not
about anything the scheduler enforces. Exact expression ruled: **`5 10-23 * * 1-5`**,
verified against the repo's own croniter (14 weekday fires, 10:05..23:05, weekends skipped,
Fri 23:05 -> Mon 10:05). I explicitly refused to add a per-task timezone: that is a new
cross-cutting scheduler capability, it would make this one task disagree with the ten
already registered, and smuggling it in under a feature directive is exactly the kind of
scope creep the design review exists to catch.

**The structural finding that makes the whole design cheap: one `side="both"` envelope
serves both consumers byte-for-byte.** Read of `evaluate_best_options` confirms each side's
section is produced independently by `_evaluate_side`, and every *shared* field
(`parameters.thresholds` for both sides, `atm_iv` computed from both buckets,
`parameters.chain.*` counts) is derived over both DTE-filtered buckets regardless of the
requested `side`. So `evaluate_best_options(side="both")["calls"]` is identical to
`evaluate_best_options(side="call")["calls"]` — a "both" result is a strict superset. That,
plus the fact that the Screener already hard-codes `dte 0..45`/`support_level=None` and the
Symbol Detail UI sends only `?side=both` (`BestOptionsView.tsx:248`), means the canonical
shape is a *constant*, not a cache-key dimension. Key is the normalized symbol alone;
category/shares/calendar are recorded as *inputs* for drift disclosure, never as key
dimensions (a per-category entry would just be a cache of stale alternatives nobody asked
for). Pinned by a required side-equivalence test — it is load-bearing.

**`:00` vs `:05` needed no coordination mechanism at all, and I confirmed why rather than
adding one.** `TaskRegistry._worker_loop` is a *single* worker thread draining one
`queue.Queue` strictly sequentially (dequeue -> sub-thread -> `join(1800)` -> next). Two
registered tasks can never run concurrently in the scheduler process, so the chain refresh
enqueued at `:00` and the precompute enqueued at `:05` are ordered by the queue, not the
clock — if the chain job overruns five minutes the precompute simply waits its turn and
still runs after it. The five-minute gap is headroom, not a guarantee, and does not need to
be. I explicitly ruled *against* an inter-task dependency ("wait for options_chain"):
blocking inside a job for another job on the same single worker is a guaranteed deadlock.
The one real residual race is disclosed, not locked: `/api/trigger/options_chain` spawns its
own raw thread *outside* the registry (bypassing its overlap guard, watchdog and `last_run`
bookkeeping — a pre-existing defect I flagged but did not fix), so a manual chain refresh can
straddle a precompute cycle; each entry's own `chain_timestamp` makes exactly that visible
per symbol.

**Atomic batch replacement AND per-symbol update — the refresh-button directive forces
both, and one rule reconciles them: the snapshot map is always replaced copy-on-write,
never mutated in place.** A full cycle builds a fresh dict in a local and publishes it with
one guarded assignment; a single-symbol refresh publishes `{**old, sym: new}`. Consumers
must take the snapshot reference *exactly once per request* — that is the entire point of
atomicity, so a Screener request iterating 200 symbols can't show 40 from generation N and
160 from N+1 without saying so. Retention ruled explicitly: a symbol that fails in cycle N+1
**carries its cycle-N envelope forward** with `status="stale"` and its original
`generation`/`computed_at` intact — a transient Cosmos or provider hiccup must never empty a
page that worked sixty minutes ago.

**Key call on invalidation: detect and disclose, never chase.** I rejected event-driven
invalidation hooks in every mutation path (symbol edit, position change, calendar sync) —
too many write surfaces, too easy to miss one, and a missed hook fails *silently*, which is
strictly worse than visible staleness. Drift is instead reported per request under a hard
constraint I imposed: **zero added Cosmos queries.** Both handlers already have `category`
and `total_shares` in hand (`sym_doc` for the 404 guard; `list_symbols()` for the batch), so
those are free — and the two calendar lookups Symbol Detail does today (plus the Screener's
whole `get_calendar_events()` scan) exist *only* to feed the evaluator and are deleted on
the cache-hit path. Calendar drift therefore goes undetected until the next cycle: the right
trade for two Cosmos round-trips per page load, forever, against a field that changes a
handful of times a year and self-corrects within the hour.

**The sharpest hazard, and it is currently safe only by accident of good style.** Today's
memo is per-request, so in-place mutation of a row is invisible; a process-wide cache makes
every such mutation permanent and cross-request. Audited every write path and found the
existing code already copy-correct — `options_screener` does `tagged = dict(row)` and
`dict(nearest_miss)` before tagging, `web/app.py`'s `row["no_shares_held"]`/`["chain_stale"]`
land on those copies, and filtering/sorting/pagination build new lists throughout
(`sorted(...)`, comprehensions, slices — no in-place `.sort()`, no slice assignment
anywhere). That accident must become an enforced invariant: envelopes are read-only after
publication, per-request metadata is attached by constructing a new outer dict
(`{**envelope, "cache": {...}}`, never `envelope["cache"] = ...`), per-row enrichment
requires a shallow copy first, and **no deep copy anywhere** — at 400 rows x 2 sides x N
symbols it would cost more than the evaluation this design exists to eliminate.

**"No request computation" written so it is testable rather than aspirational.** No
*implicit* request — page load, poll, navigation, filter, sort, pagination — may reach
`evaluate_best_options`. Exactly two explicit, response-labelled exceptions survive: the
per-symbol Refresh action, and a Symbol Detail request carrying a genuinely non-canonical
query override, which computes live and returns `cache: {"used": false, "reason":
"non_canonical_parameters"}`. **I kept the override deliberately:** this morning's 45-day
alignment decision explicitly preserved the `dte_max` override and `exceeds_system_dte_cap`
on the stated ground that they "remain live and meaningful the instant a caller explicitly
widens the window" — deleting that path under cover of an unrelated caching directive would
have quietly clawed back a still-binding decision. The UI never sends those params, so the
exception is unreachable from normal use. Corresponding acceptance test: monkeypatch
`evaluate_best_options` to *raise*, and both canonical endpoints must still answer 200 with
full rows.

**Chain warming kept on one endpoint and removed from the other, deliberately asymmetric.**
Symbol Detail's cold-chain path still calls `schedule_background_refresh` — one
explicitly-requested symbol is bounded and user-intent-driven, it is pre-existing behavior
with a passing test, and a chain fetch is not a *scoring* computation. The Screener's
request-side warming is deleted outright (`_SCREENER_MAX_COLD_WARMS_PER_REQUEST`, `to_warm`,
the whole cap): fanning out N warms from one aggregate request is precisely what that cap
existed to contain, and the chain scheduler already covers the whole universe at `:00`.

**Multi-process limitation stated, not softened, because it is the real cost of this
phase.** `run.py`'s default is genuinely one process (uvicorn single worker + scheduler
daemon thread), so the module singleton really is shared — but `--web-only`/`--scheduler-only`
as two processes, `--workers N>1`, or any replica set breaks it *silently and completely*,
and unlike `OptionsChainCache` there is **no persistence tier to rehydrate from**. That
asymmetry is the headline limitation. Mitigation for this phase is diagnosability:
`GET /api/health/best-options` reports `status:"empty"`/`generation:0`, which is exactly the
split-process signature.

**Load-bearing test coupling named up front so it isn't hit mid-implementation.** There is
**no scheduler-registry test file anywhere in `backend/tests/`** — the new adversarial suite
builds its harness from scratch, nothing to extend. `test_best_options_endpoint.py`'s
cold-cache test asserts *exact dict equality* on the warming body, so adding `reason`/
`next_run` breaks it; its `TestWarmCacheFullTable` pair warms only the *chain* cache and will
now get `warming`; and its endpoint-vs-direct parity test pops only `evaluated_at`, so a
cached envelope (computed at cycle time, not request time) turns it into a flaky
two-different-`now` comparison. Worst of all,
`test_options_screener_cache_concurrency.py`'s entire premise — "does cold-miss warming still
fire from the executor thread" — is *retired* by this design and needs deliberate rework, not
a mechanical patch.

**Ownership, with one deliberate split to keep boundaries clean:** the cycle body lives in
its own new module `best_options_precompute.py` (Livingston — Cosmos/chain-cache/evaluator
seam, his standing remit) separate from the scheduler bridge in `main.py` (Rusty), which
makes the cycle independently testable without standing up a registry. Linus owns the pure
`best_options_cache.py` and the surgical `options_screener.py` change (`memo` -> caller-
supplied `precomputed`). Livingston owns `web/app.py` end to end. Rusty owns all frontend
plus `config.yaml`. Basher owns the new adversarial suite, the concurrency-file rework, and
a grep-based reviewer gate. `best_options.py` itself is **not touched** — the evaluator stays
pure and its output is what gets cached.

### 2026-08-29 (same review, addendum) — Best Options targeted Refresh: blocking design found unsafe and superseded

Directive: `.squad/decisions/inbox/copilot-best-options-symbol-refresh-only.md` (Refresh
button on Symbol Detail → Best Options only; recompute and atomically replace that symbol's
shared entry; duplicate/in-flight protection; visible status; nothing on the Screener).
Design updated in place: `.squad/decisions/inbox/danny-best-options-scheduler-design.md` §9b.

**I had this wrong the first time and the code caught it.** My initial §9b had the handler
`await chain_cache.refresh(symbol)` inline and return the fresh envelope, reasoning that
"blocking for a few seconds is correct UX for an explicit single-symbol action." Reading
`OptionsChainCache` disproved the "few seconds" premise: `refresh()` applies **no timeout of
any kind** — the 90s `_REFRESH_SYMBOL_TIMEOUT` belongs to `refresh_all`'s thread pool
(`future.result(timeout=...)`), not to `refresh()` — and it holds the symbol's OS lock across
both provider fetches *and* the Cosmos shard write. A hung provider socket hangs the awaiting
request for as long as the socket hangs. Worse, the obvious fix is explicitly forbidden by a
frozen contract: `schedule_background_refresh`'s own docstring states no cancellation or
timeout may be layered around a refresh because it "would abandon that lock/write rather than
make the caller more responsive — the exact hazard the accepted design calls out for why
`get_or_hydrate` itself must never wrap `get_or_load_async` in `asyncio.wait_for`."
`wait_for(shield(...))` would technically survive (only the waiter gives up, the fetch
continues), but inventing new timeout semantics on a deliberately timeout-free seam to serve
a button is not a trade worth making.

**Accepted instead: non-blocking POST, status on the existing GET.** The directive's own
words ("visible status") are poll-shaped, so this is not a compromise. `POST
/api/symbols/{symbol}/best-options/refresh` returns immediately with
`refreshing`/`already_refreshing`; the work runs on an `asyncio.create_task` background task
that awaits the unbounded refresh exactly as that seam requires — an unbounded fetch now
costs a background task rather than a hung client. Status rides on the `cache` block of the
GET the page already polls (`refreshing`, `refresh_started_at`, `refresh_completed_at`,
`refresh_error`); no second status surface invented. **The GET keeps serving the previous
entry throughout — a refresh in progress never blanks the table.**

**Does targeted refresh also refresh the chain? Yes — and this was the load-bearing call.**
Recompute-only was rejected on the evaluator's own contract: `evaluate_best_options` is pure
and "identical input produces byte-identical output," so against an unchanged chain and
unchanged inputs a recompute-only Refresh returns a byte-identical envelope differing only in
`evaluated_at`. The button would visibly do nothing. The staleness a user reacts to when
pressing Refresh is the *chain's* age, not the evaluator's. I also rejected a
skip-if-not-stale heuristic: the chain TTL is 1800s, so for the first 30 minutes after every
`:00` cycle — most of the time — Refresh would silently degrade into exactly the
recompute-only case just rejected. Predictable beats clever: always fetch. One symbol, and
the in-flight guard (not a TTL heuristic) is what bounds abuse.

**Chain refresh best-effort, recompute mandatory.** Two reasons the recompute must still run
when the fetch fails: (1) category/`total_shares`/calendar can have drifted since the last
cycle — that is precisely the drift case §6 detects but deliberately does not auto-correct,
and Refresh is its sanctioned remedy, fixable by recompute alone; (2) an explicit user action
must never leave the entry *worse* than it was, so the recompute proceeds against
last-known-good, `refresh_error` is reported, and the status is never downgraded below its
previous value.

**Duplicate protection and an asyncio footgun solved by one structure.** The per-symbol
in-flight registry holds the `asyncio.Task` — `chain_cache.refresh()` already dedupes the
*fetch* on one loop, but that covers neither the evaluate-and-publish step nor a concurrent
full cycle. Holding a strong reference also fixes the classic fire-and-forget hazard where a
task nobody references can be garbage-collected mid-flight; `_schedule_background_refresh` in
the chain cache has exactly that shape today (pre-existing, flagged not fixed). Removal in a
`finally` so a crashing task can never wedge a symbol as permanently "refreshing."

**Precedence stated rather than fenced.** A targeted refresh and a full cycle run on
different threads (request loop vs. registry worker) and can overlap. **No ordering fence:
last writer wins**, both publications carry `computed_at` so the winner is observable rather
than inferred. The window is seconds, both results are current, and a fence would add
cross-thread coupling to prevent an outcome that is not actually wrong.

**Ownership boundary sharpened by this directive:** the in-flight registry holds live
`asyncio.Task` objects, so it must **not** sit in Linus's `best_options_cache.py`, which is
specified as a pure data structure. Linus exposes only the `replace_symbol()` copy-on-write
primitive and stores refresh state as plain data; Livingston owns the task registry and the
orchestration in `best_options_precompute.py`, keeping every asyncio concern behind one
owner. Rusty owns the button, its `cache.refreshing`-driven disabled state, and the new BFF
POST route.

**New gate items:** no `asyncio.wait_for`/`asyncio.timeout`/cancellation layered around
`chain_cache.refresh()` anywhere in new code; the background task is always stored in the
in-flight registry (never a bare discarded `create_task`); and the Screener has no refresh
control *and no reachable refresh endpoint* — grep the API surface too, not just the
component, since the instinct when adding a row-level action is to mirror it into the
aggregate table. Regression test named explicitly for the rejected reading: a Refresh against
a chain whose contents changed must yield a *different* envelope.

### 2026-08-29 (same review, second addendum) — Options Screener strictly precomputed-only: N-of-X contract pinned

Directive: `.squad/decisions/inbox/copilot-options-screener-precomputed-only.md`. Design
updated in place: `danny-best-options-scheduler-design.md` §11b (rewritten as a full contract),
§12.8 (new readiness test cluster), §13 (Rusty gains `types/screener.ts`).

**Strengthened my own draft to zero chain-cache coupling.** I had kept a live
`chain_cache.is_stale(symbol)` call to populate each row's `chain_stale`, reasoning it was a
cheap in-memory lock read and therefore not "hydration." Dropped it: the entry already stores
`chain_stale_at_compute` from cycle time, which is both cheaper *and more correct* — it
describes the chain as of the evaluation the row actually came from, not as of an unrelated
later moment. The endpoint now references the option-chain cache **not at all**, which converts
a judgement call ("is `is_stale()` really hydration?") into an absolute, greppable gate. Worth
recording as a pattern: when a rule's boundary requires arguing about an edge case, look for
the variant that removes the edge case entirely.

**`X` honours the `symbols=` whitelist; `N` is filter-invariant.** X = `list_symbols()`
intersected with the explicit whitelist when supplied — reporting "3 of 200 loaded" when the
caller asked for three symbols would be nonsense, and `filters.symbols` already echoes which
case applies. But X and N are untouched by `preferences`/metric filters/`sort`/pagination.
**The key ruling: a symbol that is loaded but whose rows are all filtered out still counts
toward N.** Otherwise the readiness number drifts every time the user touches a filter,
conflating "not computed yet" with "nothing of yours matched." That is the exact conflation
`options_screener.py`'s own docstring already refuses to make for `nearest_miss` ("that would
conflate 'your category rules found nothing' with 'you asked for a narrower view than what
exists,' which are different facts") — same principle, same module, applied one level up.
Regression test: identical snapshot, wide vs. deliberately over-narrow filters, `loaded`/`total`
unchanged while `pagination.total_matching` moves.

**Stale entries count toward N — and the labelling that earns it is discharged concretely.**
The directive permits it "only if design clearly labels age/status." Excluding them would be
the worse lie: they have usable envelopes and *do* contribute rows, so a header calling them
"not loaded" while their rows sit on screen is a direct contradiction. Labelling on three
surfaces: `loaded` decomposed into `loaded_fresh` + `loaded_stale` (a first-class number, not
something inferred from the detail list); per-symbol `status`/`generation`/`computed_at`/
`chain_timestamp` (age as a timestamp, not an adjective); and a per-row `entry_stale`.

**Three staleness channels now coexist and must never be merged** — named explicitly because
collapsing two of them is the obvious mistake: `stale` (this contract's quote older than 24h,
`best_options.py`'s own per-contract flag), `chain_stale` (the whole chain was past its refresh
TTL, now sourced from `entry.chain_stale_at_compute`), and the new `entry_stale` (this symbol's
result is carried forward from an older cycle). Test: one fixture where all three take
independent values on a single row.

**Retired `cold`/`warming`, kept the distinction where it is actually useful.** With no
request-side hydration there is exactly one not-ready state, so the existing
`counts: {ok, warming, cold, error}` is replaced rather than extended — keeping two dead keys to
avoid touching a frontend that has to change anyway (to render `N of X loaded`) would be false
compatibility. The `chain_cold` vs `precompute_pending` distinction survives per-symbol in
`detail[].reason` for diagnostics but deliberately **not** as a top-level count: it is not
actionable from this page, since the Screener has no refresh control, and splitting the counter
would ask users to distinguish two states they cannot act on differently.

**Exact UX copy written into the design so it is assertable rather than improvised**, reusing
the existing amber `role="status"` banner per the standing visual-consistency directive. Two
edge cases named that a naive implementation gets wrong: `X == 0` must read "No symbols
configured", never `0 of 0 loaded`; and the readiness line renders **always** when `X > 0`,
including at `N == X` — a number that only appears when something is wrong teaches users to
distrust its absence. The headline requirement: `N == 0` is a first-class, explicitly-worded
state, never a generic "no results match your filters" empty table. Conflating an unpopulated
cache with an over-narrow filter is the single worst failure mode this directive exists to
prevent, and it is exactly what the current empty-table path would do if left alone.

**Invariants promoted to tests:** `loaded + pending + error == total` and
`loaded == loaded_fresh + loaded_stale`, over every fixture including the empty one; plus an
assertion that the row `symbol` set is a subset of the loaded set (no pending/error symbol ever
contributes a row).

### 2026-08-29 — BEFORE Design Review ceremony (revalidation)

Revalidated the accepted Best Options precompute design against HEAD e3a20a2. All 7 key assumptions confirmed (registry count, price_forecast reschedule pattern, screener call sites, frontend query params, new-file clean slate, test breakage targets, no conflicting dirty state). Produced ceremony summary with dependency graph: Linus first → Livingston + Rusty parallel → Basher gate. Output: `.squad/decisions/inbox/danny-best-options-scheduler-resume-review.md`.

**Learning:** When resuming a multi-session initiative, re-checking source-level assumptions (line numbers, function signatures, import paths) against the exact HEAD commit catches drift that a design doc alone cannot. The 10 → 10 registry count and the `app.py:4595` reschedule call both confirmed — but either could have changed between sessions.

## 2026-08-29T18:23:00Z — Best Options Scheduled Precompute: BEFORE Design Review (revalidation)

**Ceremony:** BEFORE Design Review, formal revalidation against HEAD e3a20a2

**Result:** ✅ Design CONFIRMED — no conflicts found, implementation authorized to proceed in parallel across 4 ownership slices

**Verified against committed code:**
- Scheduler registry: 10 tasks registered, single worker loop ✅
- Reschedule pattern: price_forecast model followed ✅
- options_screener.py structure: clean surgical change possible ✅
- Frontend contracts: BestOptionsView & OptionsScreenerView ready ✅
- No conflicting in-flight changes ✅

**Binding directives (3, all incorporated into design):**
1. Precompute per symbol on scheduler, shared cache, Settings config, cron "5 10-23 * * 1-5"
2. Manual Refresh on Symbol Detail ONLY, no refresh on Screener
3. Screener never computes missing entries, shows "N of X loaded"

**Ownership slices authorized:**
- Linus (Quant): Cache module + screener update
- Livingston (Integration): Precompute cycle + validation
- Rusty (Frontend): Scheduler bridge + UI
- Basher (Review): Gate oversight

**Key decision:** No per-task timezone capability — design correctly ruled out, inherits container TZ semantics from existing 10 tasks.


### 2026-08-30 — Dashboard UX: Integrate Alpha Provenance into Recent Column

**Context:** User request to remove the separate "Rec." column and integrate recommendation provenance into the existing "Recent" column.

**Changes:**
- Modified `frontend/src/components/DashboardAgentTables.tsx`:
  - Removed "Rec." header column from watchlist agents table
  - Removed separate "Rec." cell that displayed SELL + ALPHA badges
  - Enhanced `RecentCell` component to accept `recommendationSource` prop
  - Added logic to conditionally show ALPHA tag alongside the first activity badge when:
    - The most recent activity is a SELL recommendation
    - AND `recommendation_source === "alpha"`
  - Maintained all existing click/navigation behavior for activity badges
  - Column count now matches between header and body for all table variants (position monitor, buy tracker, watchlist)

**Implementation details:**
- ALPHA tag uses existing `styleFor("purple")` badge styling for consistency
- Provenance tag only appears when appropriate, avoiding misleading attachment to historical WAIT/ERROR activities
- No backend changes required; all logic is frontend rendering based on existing data contract

**Validation:**
- TypeScript compilation: ✅ `npx tsc --noEmit` passes with no errors
- Git diff: 21 insertions, 11 deletions (net +10 lines)
- No breaking changes to table structure or empty/loading state handling

**Pattern learned:**
- When consolidating UI columns, ensure provenance/metadata tags are contextually relevant to the specific activity they describe, not just conditionally rendered based on row-level flags.

### 2026-08-30 — Chain-Aware Best Option Validation (design review, no implementation)

Decision: `.squad/decisions/inbox/danny-chain-aware-validation-design.md` (accepted). User directives require Alpha to receive normal CC/CSP chain context (not Best Options filtered), and validation to return WAIT or SELL where SELL may use the requested contract or a nearby real alternative found by relaxing one parameter.

**Critical finding during code inspection: both reviewer approval checks in `run_contract_validation` are dead code.** `agent_runner.py:4394` checks `net_assessment == "APPROVE"` — Supervisor's schema emits `ORIGINAL_HOLDS`/`RECONSIDER`, never `APPROVE`. Line 4420 checks `alpha_view.get("recommendation") == "APPROVE"` — Alpha's schema emits `opportunity_strength` (STRONG/MODERATE/NONE), never `recommendation`. Both always evaluate `False`, so every SELL from the primary agent is silently downgraded to WAIT. This is fail-closed (correct direction) but means the entire Supervisor+Alpha review pipeline in contract validation has been inert since shipping.

**Architectural tension resolved: deterministic post-Alpha validation (D4), not a second LLM pass.** When Alpha proposes an alternative contract, the selected contract must be validated before SELL. Options considered: (a) run Primary+Supervisor again against the alternative, (b) run a lightweight validation LLM, (c) deterministic programmatic gates. Chose (c) — a programmatic `_validate_alpha_alternative` function checks 10 gates: exists in chain, same side, not identical to requested, single-parameter relaxation only, proximity, DTE ≤ 45, no spanned earnings, delta in band, complete quote, premium floor. This is cheaper (<1ms vs ~5s), reproducible, and cannot hallucinate. Supervisor's RECONSIDER remains a hard SELL veto regardless of Alpha's alternative.

**Chain context reuse is straightforward.** The normal CC/CSP Alpha path already builds chain text via `_build_alpha_options_chain` (apply_agent_view → filter_by_type → filter_by_delta). Validation replicates this exact pipeline from the cached chain instead of `fetcher.fetch_all`, producing byte-semantically identical output. No Best Options scoring/ranking applied per user directive.

**Ownership split kept conflict-free.** Rusty owns `contract_validation_integration.py` exclusively (chain context builder, D4 validator, persistence). Linus owns `agent_runner.py:run_contract_validation` exclusively (approval check fixes, Case A-F state machine, result dict augmentation). Interface contract frozen: one new input parameter (`chain_context_text: str`) and five new output fields. Livingston owns the cross-seam integration test.

**Reusable lesson: when an LLM output schema has an approval/endorsement field, verify the code actually reads the field name the schema defines, not an assumed conventional name.** The `"APPROVE"` checks were plausible-looking code that compiled, tested green (because the fail-closed default is WAIT, which is a valid outcome), and passed review — but never matched any real model output. Schema-to-code field-name parity needs an explicit test, not inference from behavioral tests.

### 2026-08-31 — Calendar Parity Retrospective (Basher rejection → Rusty revision)

**Context:** Basher rejected Livingston's implementation of the full-context-parity calendar extractors (3 findings). Danny conducted post-rejection retrospective.

**Root causes identified:**
1. **CRITICAL — Extractor-provider shape mismatch.** `_extract_earnings_from_overview` and `_extract_exdiv_from_dividends` read flat top-level keys (`earningsTimestamp`, `exDividendDate`), but real `_build_overview`/`_build_dividends` output nests dates under `fundamentals.earnings_release_next_date_fq.value` and `dividends.ex_dividend_date_recent.value` respectively. Live yfinance dates always return `None`; Cosmos fallback silently activates, reproducing the original bug.
2. **CRITICAL — Unbound `error_msg` in outer exception handler.** Reference to Step-4-only variable in the catch-all handler → `NameError` on pre-Step-4 failures → WAIT activity never persisted. Dead duplicate code block after `return` compounds the issue.
3. **HIGH — Invented test fixtures.** All flat JSON shapes (`{"exDividendDate": "..."}`) match extractor expectations but not real provider output. 167 passing tests = false confidence.

**Ownership resolution:** Livingston locked out (authored the buggy code). Rusty assigned as revision author. Basher re-reviews.

**Decision:** `.squad/decisions/inbox/danny-calendar-parity-retrospective.md` — contains exact extractor specs (nested navigation, epoch handling, formatted fallback), exception-flow cleanup spec, provider-shape integration test requirements, and narrow acceptance gate (10 criteria). Rusty authorized to modify only `contract_validation_integration.py` (extractors + exception handler) and `test_contract_validation_calendar.py` (fixture rewrite).

**Reusable lesson: test fixtures that hand-author the shape of an upstream dependency's output are inherently fragile. Integration tests must call the actual builder to produce fixtures — if the builder changes shape, the test fails immediately instead of silently diverging.**

## 2026-08-31 — Best Options Validation: Full Context Parity (Design + Retrospectives)

**Context:** User reported Best Option validation missed an ex-dividend date present in calendar, requesting parity with normal Following coverage. Linus/Rusty audits found validation copied chain context but omitted canonical Following overview, technicals, forecast, dividends, enrichment, volatility.

**Owner:** Danny (Lead) — Design, retrospective analysis, acceptance gate specification

**Work Items:**

### (1) Design: Full Market-Context Parity (ACCEPTED)
- **Document:** `.squad/decisions.md` (merged from inbox)
- **Core principle:** One canonical `fetch_all(force_refresh=True)` — feeds full 6-page block + contract evidence to all three agents (Primary, Supervisor, Alpha)
- **Chain reuse:** Single refresh boundary. `fetch_all` refreshes chain internally; old `_force_chain_refresh` call removed (duplicate)
- **Fail-closed SELL:** If `fetch_all` fails → WAIT + `error=full_context_unavailable`, not silent fallback
- **Calendar robustness:** Live yfinance dates primary; Cosmos fallback with provenance logging
- **Immutable contract evidence:** Separate labeled section in prompt, never replaced

### (2) Retrospective: Validation Suite Hang (4h+ deadlock)
- **Root cause:** Line 863 `_execute_validation` bypasses injected `context_provider`, calls global `get_shared_provider()` singleton
- **Evidence chain:** app.py → ContextProvider → start_validation(context_provider=...) → _execute_validation(...) → **ignored parameter** → `get_shared_provider()` → real network I/O
- **Classification:** Production DI defect + test false-patch + event-loop deadlock (all three compound)
- **Impact:** 4h+ CI hang at 74% suite completion, >100 blocked test runs
- **Fix spec:** Provider must be explicit parameter to both `start_validation` and `_execute_validation`, injected by caller. Tests inject mock provider at seam
- **Ownership:** Livingston (contract_validation_integration.py + test files fix)
- **Document:** `.squad/decisions.md` (merged from inbox)

### (3) Retrospective: Calendar Parity Extractors (Basher rejection)
- **Trigger:** Basher rejected Livingston's full-context-parity calendar extractors (3 findings)
- **Root causes (CRITICAL):**
  1. **Extractor-provider shape mismatch:** Extractors read flat top-level keys (`earningsTimestamp`, `exDividendDate`), but real `_build_overview`/`_build_dividends` output nests dates under `fundamentals.earnings_release_next_date_fq.value` and `dividends.ex_dividend_date_recent.value`. Live yfinance dates always `None`; Cosmos fallback silently activates → original bug reproduced
  2. **Unbound `error_msg` in outer exception handler:** Reference to Step-4-only variable in catch-all → `NameError` on pre-Step-4 failures → WAIT activity never persisted. Dead duplicate code block after `return` compounds issue
  3. **Test fixture fragility:** All flat JSON shapes match extractor expectations but not real provider output. 167 passing tests = false confidence
- **Ownership resolution:** Livingston locked out (authored buggy code). Rusty assigned as revision author
- **Spec:** Exact extractor behavior (nested navigation, epoch handling, formatted fallback), exception-flow cleanup, provider-shape integration test requirements (4+ new tests), acceptance gate (10 criteria)
- **Document:** `.squad/decisions.md` (merged from inbox)

**Reusable Lessons:**
1. When an LLM output schema has approval/endorsement field, verify code reads exact field name (discovered dead approval checks in `run_contract_validation`)
2. Test fixtures hand-authoring upstream dependency output are inherently fragile — integration tests must call actual builder
3. Provider injection seams must be explicit parameters all the way down, not implicit module-level singletons

**Artifacts:**
- `.squad/decisions.md` — 3 new sections (design + 2 retrospectives)
- `.squad/decisions/inbox/` — REMOVED (merged into main)

**Interdependencies:**
- Holds on Livingston's fix (provider hang + test fixture rewrite)
- Holds on Rusty's revision (calendar extractors + exception flow)
- Both gate on Basher's second review
