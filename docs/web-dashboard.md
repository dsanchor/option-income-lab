# Web Dashboard

[← Back to README](../README.md)

- **Dashboard** (`/`) — Alerts overview by agent type with rolling time-range counts (today, last 7 days, last 30 days), scheduler status, recent activity feed with alert indicators and clickable links, position summary. Per-agent **Run Analysis** actions execute only that dashboard agent. The trigger response includes a `run_id`; `/api/dashboard/status` exposes run-ID-scoped `runs` plus deterministic `agent_statuses` aggregates, and updates an agent's `last_run` only after successful completion. The button follows its own run to success/failure instead of treating POST acceptance as success. Completed run details are process-local and bounded to the newest 100 records. These actions remain distinct from Settings scheduler-task **Run Now** controls. Activities can be filtered by **confidence level** (high/medium/low) and **agent type** for granular views. WAIT activities with MODERATE or STRONG supervisor opinions display a 🤔 indicator icon (STRONG gets a pulse animation).
- **Alert Details** (`/alerts/{agent}/{symbol}`) — All alerts for a specific symbol, newest first, with activity badges and risk flags.
- **Alert + Activities** (`/alerts/{agent}/{symbol}/{index}`) — Full alert JSON and backing activities from the same time window.
- **Symbol Detail** (`/symbols/{symbol}`) — Full detail page for a symbol: expandable positions with source traceability, editable notes field, Close/Roll/Delete actions, activities, alerts, and "Open Position from Alert" / "Roll Position from Alert" buttons on activity detail. Active option positions include **Simulate a Roll**, an informational exact-contract calculator that compares current-close and target-open robust midpoints for the full position, exposes quote provenance/staleness, excludes commissions, and never places or records a trade. Features a **play button** (▶) for running individual symbol analysis on demand. **Generate Report** and **Chat** buttons are aligned right; watchlist toggles are aligned left. A **Pause until earnings** button suspends the following agents (Covered Call, Cash-Secured Put, Buy Tracker) until the symbol's next stored earnings date to save LLM tokens; while paused the three toggles render shadowed/disabled with a `⏸ Paused until earnings · <date>` badge and a **Resume now** button, and the symbol's rows appear shadowed on the main dashboard. Pauses auto-clear the day after earnings (query-level auto-expiry plus a daily reactivation job). Activities support confidence and agent-type filtering. WAIT activities with MODERATE or STRONG supervisor opinions display a 🤔 indicator icon. Activity detail includes collapsible "Supervisor" and "Alpha Advisor" panels with color-coded badges showing audit findings and aggressive alternatives.
- **Symbols Watchlist** (`/symbols`) — Central watchlist management page showing all tracked symbols with enrichment data. Columns: Symbol, Category, DGI Score, Tech Timing, Entry (technical timing tag), Momentum (directional signal), Price, Shares (inline editable), In Calls, Put Exposure ($committed if assigned). The compact **Momentum** multi-select filters by one or more backend-authored momentum labels; its default **All** state shows every Symbol, and **Unknown** includes missing or unrecognized legacy values. It combines with search, historical visibility, and the existing **signal filter pills** for actionable categories:
  - **All** — Show everything
  - **Ideal Puts** — (SB/Buy + Bullish/Neutral/Weakening) or (any + Oversold)
  - **Ideal Calls** — (Hold/Wait + Weakening/Bearish/Neutral) or (any + Overextended)
  - **Accumulate** — Accumulate + Bullish/Neutral (small DCA)
  - **⚠️ No Puts** — SB/Buy + Bearish pure (falling knife)
  - **⚠️ No Calls** — Wait + Bullish pure (runaway)
- **Symbol Report** (`/symbols/{symbol}/report`) — Dedicated report display page showing the latest generated report for a symbol (technical analysis, dividends, options chain, risk assessment, and recommendations).
- **Symbol Chat** (`/symbols/{symbol}/chat`) — Per-symbol chat page with a context selection screen before starting the conversation. Pre-loads market data via the yfinance provider for faster responses. Supports open call and open put analysis contexts.
- **Forecast Report & Chat** (`/symbols/{symbol}/forecasts`) — Inline, expandable report and follow-up chat scoped only to the selected symbol and 1d/7d/30d/90d forecast-history range. The backend rebuilds bounded context from persisted forecast rows, calibration, hit rates, and rolling averages on every request; browser-authored market/portfolio context is never accepted. Conversations remain ephemeral. Its independently configurable AI Providers function id is `forecast_report_chat` and its default Azure deployment is `gpt-5.6-luna`.
- **Fetch Preview** (`/symbols/{symbol}/fetch-preview`) — Debug page showing raw market data for each resource (overview, technicals, forecast, options chain) with fetch timing and size.
- **Chat** (`/chat`) — Dual-mode chat experience powered by your configured LLM provider (Azure or Gemini):
  - **Portfolio Chat** — Analyze tracked symbols using CosmosDB data (watchlists, positions, recent activities). Click "Portfolio Chat" to ask questions about your tracked symbols.
  - **Quick Analysis** — Analyze any symbol (tracked or not) by fetching live Yahoo Finance data without saving to the database. Click "Quick Analysis", select a market (NASDAQ/NYSE/AMEX/OTC), and get instant analysis without committing to tracking.
  - Mode selector on the chat page lets you switch between modes at any time.
- **Settings** (`/settings`) — Scheduler config, Telegram notifications toggle & test button, Summarization Agent config (cron schedule & activity count), runtime stats (today/7d/30d telemetry), a Debug Data Fetch tool for testing data fetching per symbol, and an **Agent Chain Pipeline** debug view (`/api/debug/agent-chain/{symbol}`) for inspecting the full two-phase monitor pipeline per symbol. Each scheduled agent (Monitoring, Calendar Sync, Watchlist Reactivation, Options Chain, DGI Screener, Summary, Portfolio Enrichment) has a **Run Now** button for manual triggering. Settings are persisted to CosmosDB and survive application restarts and deployments. Changes made in the Settings UI are immediately available to all components (scheduler, telegram notifier, summarization agent, etc.) without requiring a restart.
- **DGI Screener** (`/dgi`) — Top 20 dividend growth stock candidates ranked by composite quality score. Color-coded category badges, per-row Quick Analysis (▶) and Add to Symbols (➕) actions. Configurable stock universe and filter thresholds via Settings.
- **Economics** (`/economics`) — P&L analytics dashboard for options trading performance. Features:
  - **Summary cards** — Total premium collected, total buyback costs, net income, weighted average RoC% (annualized), and win rate
  - **Filters** — Year, months (multi-select dropdown with checkboxes), symbols (multi-select dropdown with checkboxes), type (call/put), status (closed/rolled). Defaults to current year on load.
  - **Monthly breakdown table** — Net income, premium, buyback, RoC%, and win rate per month
  - **By-symbol breakdown table** — Same metrics grouped by underlying symbol
  - **Charts** — Monthly Net Income stacked bar chart (calls vs puts breakdown) and Calls vs Puts donut chart
  - **Positions detail table** — All matching positions with per-share and dollar amounts, individual RoC%, days held, and status
  - **Weighted RoC%** — Calculated as `total_net_income / sum(strike × 100)` to avoid misleading simple averages. Annualized using average days to expiration.
  - **Contract multiplier** — Premium and buyback are stored per-share; Economics displays total dollar amounts (×100 for standard options contracts)
- **Events Calendar** (`/symbols/calendar`) — Monthly calendar view showing earnings and ex-dividend dates for all tracked symbols. Color-coded by event type and position exposure (purple/orange for earnings, red/yellow for ex-dividend). Data cached in CosmosDB with configurable daily sync from Yahoo Finance. Includes a manual Refresh button for on-demand updates.

---

## Correcting an option expiration

Expand a position under **Symbol Detail → Options** and use **Expiration → Edit**.
Choose the correct date, then **Save**, or **Cancel** (Escape also cancels).
The date must be a real ISO `YYYY-MM-DD` date; historical dates are allowed,
including for closed or rolled positions. This corrects the existing position:
it does not execute a roll, reopen it, change premiums/buyback costs, alter
linked ledger movements, or replace its ID or roll-chain links.

The browser refreshes the position after saving and resets its local monitoring,
DPS, and roll-analysis panels. Historical activities and snapshots remain stored,
but observations from before the correction (or for a different expiration) are
not reused as current monitoring or DPS trend inputs. New monitoring rebuilds
those views; old-contract decisions are also excluded from monitor prompt
context. The correction boundary is included in user-data backups.
Shared symbol-level market-data caches remain valid and are not
purged. If another edit changes the symbol concurrently, refresh and retry.
The API is `PATCH /api/symbols/{symbol}/positions/{position_id}/expiration` with
`{"expiration": "2026-10-16"}`.

## Position contract quantities and historical repair

The positions table and expanded detail show the same backend-resolved **Total**
and **Remaining** contract counts, including an explicit **Unknown** with the
resolution error rather than hiding missing/null/invalid data or defaulting it
to one. Sources are available in the quantity tooltip. Historical estimates
carry a visible warning. Symbol list, raw symbol, detail, and position-mutation
responses add a read-only `contract_counts` projection; persisted `contracts`,
aliases, premiums, buybacks, and ledger accounting are unchanged by reads.

Original totals use `contracts` / `quantity`; same-tier aliases must agree.
Remaining counts use `open_contracts` / `contracts_open` first, as do both roll
views and roll persistence. Signed short counts and numeric strings normalize
through the shared Python resolver. A remaining-only count does not establish
the original total. Closed (including assigned/expired) and rolled positions
have zero remaining, not zero original total. Explicit partial-close/assignment
evidence without an authoritative remaining alias is unknown and cannot roll;
remaining quantity cannot exceed an otherwise valid original total.

The only implicit-one fallback remains the verified **active** historical
writer shape: no schema marker, CALL/PUT, positive finite strike, valid expiry,
notes field, generated `pos_..._YYYYMMDD_HHMMSS` identity and timezone-aware
`opened_at` matching the same UTC second, both strictly before
`2026-09-26T10:44:42Z`. Partial evidence disables that fallback. This is the
historical writer's one-contract convention, not a reconstruction of the
investor's intended manual quantity. Closed/rolled documents without explicit
quantity evidence remain unknown and are **not** guessed or reopened.

`backend/scripts/repair_position_contracts.py` materializes only **missing**
canonical `contracts` when the shared resolver establishes an original total:
an explicit, valid `quantity` alias (including closed/rolled records), or the
restricted historical fallback above. It requires unambiguous position IDs,
recognized lifecycle status/type, a supported/absent schema marker, valid
remaining evidence, and a document ETag. It does not copy a remaining-only
count into the original total, infer totals from premium/ledger/share data,
rewrite existing canonical values (even numeric strings), or repair explicit
null, invalid, conflicting, modern missing, or ambiguous partial quantities.
Existing repair provenance on a quantity-less record is also left for review,
not overwritten.
`closed_contracts`, `contracts_closed`, `assigned_contracts`,
`contracts_assigned`, `partial_closes`, and `partial_assignments` are treated
conservatively as partial-lifecycle evidence. Skips include exact reasons.

Example operator workflow, **not executed against any live database as part of
this implementation**. Use separately authorized target credentials in
`COSMOSDB_ENDPOINT` / `COSMOSDB_KEY`; the script does not load `.env`:

```bash
cd backend
# Default mode is dry-run. Repeat --symbol for an explicitly bounded batch.
python3 scripts/repair_position_contracts.py --symbol AAPL \
  --database stock-options-manager --symbols-container symbols \
  --max-documents 10 --max-positions 100 --dry-run

# Review actions, skips, target and sha256 above; reuse exactly the same scope.
python3 scripts/repair_position_contracts.py --symbol AAPL \
  --database stock-options-manager --symbols-container symbols \
  --max-documents 10 --max-positions 100 --apply \
  --plan-sha256 REVIEWED_SHA256 --confirm REPAIR_POSITION_CONTRACTS
```

For **all symbols**, replace the repeated `--symbol` flags with the explicit,
mutually exclusive `--all-symbols` scope:

```bash
cd backend  # Omit this if already inside backend.
python3 scripts/repair_position_contracts.py --all-symbols \
  --max-documents 1000 --max-positions 10000 --dry-run

# Review the complete report; paste its sha256, keeping the same target/bounds.
python3 scripts/repair_position_contracts.py --all-symbols \
  --max-documents 1000 --max-positions 10000 --apply \
  --plan-sha256 REVIEWED_SHA256 --confirm REPAIR_POSITION_CONTRACTS
```

All-mode discovery is read-only and includes **every** `symbol_config` in the
configured symbols container, even configurations without positions. It uses
their actual exact symbol partition values without uppercasing or rewriting
case-sensitive identities. The deduplicated, sorted discovered scope and all
document snapshots are bound into the plan hash. Apply rediscovers the scope;
new or removed symbols/documents invalidate the reviewed hash. An empty
container is a successful no-op and produces no backup. Discovery and planning
abort on the finite bounds above rather than silently omitting documents.
These are the maximum supported bounds; if exceeded, use explicit smaller
`--symbol` batches. No live discovery or repair was run for this implementation.

The plan hash binds the target endpoint fingerprint/database/container, exact symbols, bounds,
snapshots/ETags, candidates and skips. Bounds abort rather than truncate.
Credentials come only from the two environment variables above; missing values
fail clearly. Neither the endpoint nor the key is printed in reports; only a
SHA-256 endpoint fingerprint identifies the account. Database and container
use the existing non-secret defaults, with the explicit CLI overrides shown.
Changed plans require a new audit. Before writing, all affected originals are
re-read and saved to an exclusive, private (`0600`), fsynced and read-verified
JSON backup under `backend/scripts/migration_backups/` (or `--backup-dir` inside
the current project directory). Retain this sensitive backup securely.
Every replacement uses ETag `IfNotModified`, preserves unrelated fields, and
is read-verified afterward. No upserts/deletes or blind retries occur; concurrent
edits fail closed. The script adds schema v2 if absent and repair provenance,
retaining the historical warning after materialization.

Apply is not transactional across symbol documents: the report identifies
updated documents, CAS conflicts and failures. Exit codes are 0 for success
(including no candidates), 2 for rejected/preflight/backup failures, and 3 for
apply conflicts or verification failures. After a partial run, audit again and
review a new hash; repaired positions become no-ops. There is no automatic
restore that could overwrite later user edits: backups retain the exact
pre-write documents/ETags for separately reviewed recovery.