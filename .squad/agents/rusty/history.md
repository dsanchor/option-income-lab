# Rusty — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Role:** Backend, runner, scheduler, persistence, and frontend integration owner
- **Stack:** Python, Microsoft Agent Framework, CosmosDB, yfinance/TradingView,
  FastAPI/BFF, React

## Core Context

- Built the CosmosDB service layer, scheduler/task registry, dashboard APIs,
  symbol/position workflows, settings persistence, chat endpoints, and agent
  runner integration.
- Data-provider architecture prefetches and normalizes overview, technical,
  forecast, dividend, and options-chain data before agent execution.
- Scheduler work uses non-blocking queued jobs, overlap guards, per-symbol and
  worker timeouts, dynamic configuration, and persisted task state.
- Unified activity/alert records use `is_alert`; all downstream consumers should
  share one normalized activity object.
- Settings use CosmosDB as authoritative when configured, with ETag
  read-merge-replace, conflict retry, read-back verification, and scheduler
  reload only after durable success. YAML is authoritative only without Cosmos.
- UI/backend work includes symbol watchlists, pause-until-earnings, financial
  editing, roll tables, options-chain caching, provider/model settings, and
  portfolio chat context.

## Durable Implementation Patterns

- Normalize at read/input boundaries; malformed, non-finite, or unverified data
  remains unavailable.
- Reassert protected fields after dict spreading to avoid caller overwrite.
- Use lazy initialization/imports for expensive or provider-specific resources.
- Keep position monitors active when following-agent watchlists are paused.
- Preserve source intent: automated from-activity values and manual values have
  distinct contracts.
- Long-running scheduler jobs must not block heartbeat or next-run advancement.
- When Cosmos is configured, persistence failure is an error, never silent YAML
  fallback.

## Recent Learnings

### 2026-08-18 — yfinance normalizer cleanup applied to `options_chain_cache.py` (per Linus's request)
- Linus's `options_chain_merge.py` is done/frozen (449 tests). He confirmed
  `options_chain_cache.py` is outside his authorized artifacts and asked me
  to personally mirror, on the yfinance side, the same normalizer fix he'd
  already applied to `tv_options_chain_fetcher.py` for TV.
- Rewrote `_process_option_df`: dropped the `current_price`/`T`/
  `greeks_calc` params entirely; stopped fabricating placeholder defaults
  for `bid`/`ask`/`iv`/`lastPrice`/`lastTradeDate`/`volume`/`openInterest`/
  `inTheMoney`/`contractSymbol` — a missing/NaN yfinance value is now
  omitted from the dict (never defaulted to 0/0.0/False/""); a real
  individually-valid zero (e.g. `bid=0.0`, `volume=0`) is still written
  through unchanged. Removed all `mid`/greeks computation from this
  function — that's now `recompute_derived`'s sole job, already wired into
  `_refresh_locked` from my earlier persistence work. No `_meta` written
  here either (unchanged: only `merge_prior`/`recompute_derived` write it).
  Removed now-dead `_get_greeks()`/`self._greeks` and the now-unused
  `robust_mid` import as a consequence.
- Verified with a throwaway script (deleted after use, never committed)
  exercising the real `_fetch_yfinance`/`_process_option_df` path against a
  mocked yfinance DataFrame with a fully-NaN row, a fully-observed row, and
  a real-zero row — confirmed NaN fields are omitted, zeros are written
  through, and no mid/greeks/`_meta` appear at this stage. Re-ran the full
  focused suite (merge + store + cache + filters + roll table): **581
  passed**, unaffected — none of my own tests assert on this function's raw
  pre-`recompute_derived` output shape (all monkeypatch `_fetch_yfinance`
  wholesale), so this was a pure, test-safe internal cleanup.
- Decision recorded in `.squad/decisions/inbox/rusty-persistent-option-chain-store-impl.md` §8.

### 2026-08-18 — Persistent Option Chain: Basher Review Hidden-Edge Follow-up
- Added 11 more deterministic tests (store + cache) for edges Basher flagged
  before gating the diff: a cold-singleton `--web-only` replica sharing a
  store with the combined scheduler+API instance (not just "a restart");
  Cosmos CAS retry parametrized over both 409 and 412 (store already treated
  them identically, now locked in by test); `schema_version`-absent legacy
  shard hydration; a fixed-seed 40-cycle property/fuzz test proving real
  `merge_prior` never loses a contract and never regresses `quote_asof`;
  `gate_bucket`'s exact 3-contract-all-failing vs 2-failing+1-passing
  boundary; TV supplying only IV or only ask (field-level, not per-source,
  precedence); and a carried-forward contract's exact handoff shape to
  `options_math.executable_buyback_ask`.
- Corrected two of my own assumptions while writing these: (1) Danny's G5
  ("unparseable expiration keys are immortal") is actually **self-healing**
  — `merge_prior` has its own defense-in-depth rejection of malformed keys,
  identical to `merge_sources`, so a legacy junk key from a hydrated prior
  chain is silently dropped on the very next refresh, not immortal.
  (2) `_meta.carried` means "absent from live this cycle", not "degenerate
  this cycle" — a contract a source DID report, just with junk bid/ask/iv,
  is `carried=False` even though its fields are effectively carried from
  prior. Both corrections recorded in my inbox file so no one repeats them.
- Full focused suite (merge + store + cache + filters + roll table):
  581 passed.

### 2026-08-18 — Persistent Option Chain: Persistence/Lifecycle/Concurrency
- New `options_chain_store.py` (Cosmos-backed, one shard per symbol+expiration,
  ETag/CAS retry re-applying `merge_prior`, graceful no-Cosmos/local
  degradation, grace-period pruning after real expiration) plugs into
  `options_chain_cache.py` behind a per-symbol `threading.RLock` covering the
  full hydrate → fetch → merge → persist cycle.
- Coded strictly against Linus's frozen `options_chain_merge.py` seven-function
  interface; never redefined trust-gate/merge semantics. His four merge
  functions only ever return `{symbol, timestamp, calls, puts}` — any other
  top-level key (e.g. `underlying_price`, needed for greek recompute) must be
  re-added by the caller after each call, not assumed to survive.
- `threading.RLock` reentrancy is per-OS-thread, not per-coroutine: concurrent
  `asyncio.create_task()`s on one event loop share a thread and would NOT
  serialize on the same lock. This matches Danny's explicit spec choice; real
  "no lost update" safety only holds across genuinely different OS threads
  (`refresh_all`'s thread pool, or a web request thread vs. the scheduler
  thread) — tests for this must use real `threading.Thread`s, not
  `asyncio.gather()`, to exercise true contention.
- Persistence failures are always non-fatal and logged only; in-memory
  assignment happens before the persist attempt so a Cosmos outage can never
  roll back a good in-memory result. TTL controls refresh timing only —
  contract retention/deletion is governed solely by real expiration date
  (America/New_York) plus an accepted grace period.
- `test_yfinance_data_provider.py` (unowned by me) has a pre-existing gap: its
  `@patch("...yf")` doesn't cover `options_chain_cache._fetch_yfinance`'s own
  local `import yfinance`, so its options-chain assertions exercise real
  network data. That, plus Linus's now-wired "absence is not zero" contract
  semantics, surfaces field-presence assumptions in that file that need an
  update by whoever owns it — flagged in my inbox decision, not fixed by me.

### 2026-08-17 — Buy Tracker Canonical Normalization
- Adapt raw provider output into a fixed ephemeral evidence object. Only the
  five binary score dimensions are accepted; score is always recomputed.
- Apply exceptional promotion and hard-WAIT predicates before alerting,
  evaluation, persistence, summaries, tracing, and notification.
- Exact canonical risk flags are conservative fallbacks only when raw evidence
  is unavailable. Raw safe evidence overrides stale flags; prose cannot create
  positive evidence.
- Provider prompt examples and evidence paths are shared and production-shaped.

### 2026-08-17 — OpenCallMonitor Zero-Quote Safety
- Added a shared positive-finite executable-ask contract for short-call P&L,
  buyback, roll tables, candidate tables, and DPS economics.
- Invalid asks yield null economics, skip profit-only Phase 2, and persist
  deterministic non-alert WAIT without prolonged-WAIT notifications.
- Independent risk rationale remains enforceable; valid positive asks preserve
  CLOSE and ROLL behavior.

### 2026-08-10 — AI Provider Cosmos Persistence
- Settings mutations read the authoritative document, merge only intended
  fields, conditionally replace by ETag, retry conflicts, and verify read-back.
- Configured Cosmos unavailability returns failure; unrelated document fields
  are preserved and live scheduler state updates only from verified data.

### 2026-08-08 — Watchlist and Position Financial Integration
- Symbol creation and inline shares editing validate normalized inputs and keep
  forecast backfill failure isolated from durable creation.
- Position premium and buyback updates use distinct routes and strict numeric
  validation.
- Suitability categories are owned by deterministic Entry + Momentum semantics,
  independent of watchlist flags and option-chain filters.

## Validation Practice
- Run targeted pytest suites, Python compilation, focused frontend lint/type
  checks, and scoped diffs.
- Preserve unrelated baseline provider failures in reports.
- Verify runner ordering and object identity at downstream boundaries.
