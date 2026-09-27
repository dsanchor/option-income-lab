---
updated_at: 2026-09-27T19:23:18Z
focus_area: UK pence quotes normalize through the shared FMV boundary, and safe diagnostics plus legacy scrip cost repair are implemented and approved.
active_issues:
  - "⚠️ Cross-partition overview latency: Monitor `GET /api/symbols/overview` as ledger grows beyond current scale (N≤3 accounts). Currently 2 Cosmos queries; consider materialization if latency exceeds 500ms."
---

# What We're Focused On

**Implemented (UK normalization + legacy scrip repair):** Yahoo `GBp`/`GBX`
quotes are normalized to GBP through the shared authoritative quote boundary
used by both Dividend · Buy backfill and Yahoo-on-save. Economics exposes
bounded, filtered diagnostics for unvalued scrip events without changing
aggregate eligibility or math. The separate `repair_legacy_scrip_costs`
migration is dry-run-first, SHA-confirmed, backup/CAS/restore protected, and
fails closed on contradictory evidence, correction ambiguity, and duplicate
ACTIVE share legs. The summary layout now shows Total Dividends plus two
secondary cards, without a duplicate standalone Scrip Dividends card. Basher
approved the final worktree after 370 targeted backend tests and frontend,
type, lint, and diff checks passed.

**Implemented (Scrip Economics + Yahoo FMV-on-save):** Dividend Economics now
aggregates eligible scrip corporate actions at event grain. Scrip economic
value is share FMV less personal contribution, cash top-ups, and attributable
fees; cash fiscal totals and Cash Yield on Cost remain cash-only. Combined
cash+scrip totals drive averages, tables, charts, cumulative history, YoY, and
overview, with explicit Partial/Unavailable coverage and preserved zero or
negative values. Dividend Count was removed from KPI cards.

Dividend · Buy create/correction can request server-owned `YAHOO_OPEN`
valuation. The endpoints and backfill share Security Master/provider-symbol,
exact-or-next-session Open, listing-currency, historical ECB, Decimal, and
provenance logic. Valuation completes before one account-partition
transactional write, with stable request idempotency, zero partial state on
failure, and loading/retry/correction UX. Basher's exact-final-worktree gate
approved 255 backend and 129 frontend tests, TypeScript, visual parity, and
diff hygiene.

**Implemented (Lot Average Price):** Symbol Detail Stocks movements now expose
and render backend-authored `lot_average_price_eur` for ordinary Buy and
Dividend · Buy share-acquisition rows. The strict net-inclusive Decimal
contract, ZERO_COST handling, null fail-closed behavior, non-share corporate
action exclusion, and HALF_UP display rounding passed 106 backend and 125
frontend targeted tests and received final review approval.

**Released (Phase 2):** Portfolio accounts, transfers, reassignment, FX, filters — commit `08809eb` with 478 tests passing; both API and frontend revisions deployed and healthy on 2026-09-06T11:59:49Z.

**Released (Cost-Basis):** Portfolio CMP cost-basis implementation, Movements toolbar, rights sales, import safety guard — commit `ff087c3` with 209 tests passing (130 cost-basis acceptance + 79 holdings/corrections); both API and frontend revisions deployed and healthy on 2026-09-06T14:08:45Z.

**Released (Symbol Unification):** Portfolio ↔ Watchlist ↔ Symbol Details unification — unified Add Symbol UX, idempotent symbol_config enrollment (all agents/notifications disabled by default), two-section Watchlist (Portfolio symbols vs. Watchlist-only), unified Symbol Details with Portfolio holdings and movements, MIC:TICKER canonical routing with backward-compatible disambiguation — commit `803b8f3` with 114 tests passing; both API (rev 059) and frontend (rev 052) revisions deployed and healthy on 2026-09-06T15:36:33Z. **All 12 high-risk requirements verified; zero regressions (2,952 existing tests + 114 new, 100% pass).**

**Released (Portfolio Movement Workflows):** Expanded portfolio movement workflows with full audited correction for BUY/SELL/DIVIDEND, transfer/group guards, UX and validation for unit-price/trade-value/fees/effective-price, Stocks/Rights UI labels (internal ACCIONES/DERECHOS unchanged), Spanish/English CSV parsers, origin/destination withholding amounts (server-derived percentages), composite corporate actions with atomic operations and frontend wizard, Symbol Details Options/Stocks organization with full transaction history, batch reassignment reason optional (individual reason remains required), Portfolio Income Lab visible branding (infrastructure unchanged) — commit `0c6049a` with 614 tests passing (431 backend + 183 frontend); both API (rev 0000061) and frontend (rev 0000054) revisions deployed and healthy on 2026-09-06T20:57:09Z. **All release directives implemented and tested; 100% pass rate; zero regressions.**

**Next Priority:** Dividend Portfolio — Phase 1 MVP. User request: BUY/SELL/DIVIDEND ledger for multi-broker portfolio (Fidelity, HeyTrade, ING, Interactive Brokers), multi-currency (EUR/USD/GBP/CHF), withholding tracking (source + destination), mixed cash/share dividends. **Prerequisites met:** All four portfolio deliverables (Phase 2, Cost-Basis, Symbol Unification, Movement Workflows) fully stable, 687+ total tests passing, zero regressions, all phases deployed to production. Contract drafted by Danny; awaiting user confirmation on open questions.

**Secondary:** Options trading agents (covered call + cash-secured put) — deferred during portfolio MVP phase.

### Monitoring Items

1. **Cross-Partition Overview Latency** (Symbol Unification, ongoing)
   - Endpoint: `GET /api/symbols/overview`
   - Current: 2 Cosmos cross-partition queries (portfolio securities + watchlist-only)
   - Scale tested: N≤3 accounts, acceptable at current scale
   - Action: Monitor query latency as ledger grows; consider materialization if latency exceeds 500ms
   - Acceptable SLA: < 2s response time for Symbols page load

2. **Read-Repair Effectiveness** (Symbol Unification, operational)
   - Auto-enrollment during holdings compute prevents manual backfill in normal operation
   - Backfill tool available for one-time reconciliation only
   - No scheduled re-runs; read-repair ensures eventual consistency

3. **Portfolio/Watchlist Mutual Exclusivity** (Symbol Unification, verified)
   - Enforced at rendering (UI never duplicates symbol)
   - Enforced at API (counts correct, no symbol in both arrays)
   - Monitored via 18 dedicated tests; all passing
