# Danny — Design Review: Symbol Details → Stocks "Symbol Configuration" Section

## Verdict: APPROVED (design only — no implementation)

## Current-state findings

- `GET /api/symbols/{symbol}` and `GET /api/symbols/{symbol}/detail`
  (`_compute_symbol_detail` in `backend/web/app.py`) already return a
  `security` projection — but only
  `{security_id, company_name, exchange_mic, isin, listing_currency,
  status}`. **No `provider_symbols`, `cusip`, `sedol`, `country`, or
  `asset_class` is exposed today**, and there is **no SecurityMaster
  update endpoint at all** — `CosmosSecuritiesService` has
  `create_security`/`get_security`/`list_securities`/
  `find_candidates_for_name`/`_find_by_isin` only, no `update_security`.
  This section requires a genuinely new backend write path, not a
  frontend-only change.
- `PUT /api/symbols/{symbol}` (existing) already updates `symbol_config`
  fields (`display_name`, `covered_call`/`cash_secured_put`/`buy_tracker`,
  `exchange`, `telegram_notifications_enabled`, `total_shares`) and already
  enforces `enforce_us_options_eligible` (§J.4.3) on the option-toggle
  subset — this is reused verbatim for the toggle portion of the new
  section, not reimplemented.
- Neither `replace_symbol` (symbol_config writes) nor `create_security`
  currently use Cosmos ETag/`match_condition` optimistic concurrency —
  this is a pre-existing gap, non-blocking for this review, but the
  codebase does have an established ETag/`MatchConditions.IfNotModified`
  pattern elsewhere (`cosmos_db.py` ~line 1916, used by the ledger-repair/
  import path) that the **new** security_master update endpoint should
  reuse rather than skip, since it is a new write surface being designed
  from scratch.
- `provider_symbols.py::validate_provider_symbols` already exists and must
  be reused for any provider_symbols edit (no reimplementation, no new
  regex).
- `resolve_yfinance_symbol`/`resolve_tradingview_symbol` already compute
  the effective resolved symbols from override-or-MIC precedence — the UI
  must display **both** the raw configured override (if any) and the
  effective resolved value computed via these exact functions, so the
  section never silently duplicates or drifts from the resolution logic
  used by enrichment/TradingView elsewhere.
- Placement target confirmed in `frontend/src/app/symbols/[symbol]/page.tsx`:
  the `Stocks` `DetailSection` currently renders
  `PortfolioHoldingsCard` then `StockTransactionsTable`. The new
  `SymbolConfigurationCard` must render **first**, above
  `PortfolioHoldingsCard`, inside the same `{stocksSecurityId && (...)}`
  guard (i.e. it only appears when the symbol has a canonical
  `security_id` — never for a pre-security-master legacy state).
- The already-approved "batch account assignment only in Symbol Details →
  Stocks, locked to current security" feature lives in this same tab —
  the new section must be visually/structurally distinct (its own card,
  its own save action) so the two are not confused or accidentally merged
  into one form.

## Response schema — `GET /api/symbols/{symbol}` (extend `security` field)

```
"security": {
  # Read-only canonical identity (never editable via this section)
  "security_id": "XAMS:ULVR",
  "exchange_mic": "XAMS",
  "ticker": "ULVR",

  # Editable metadata
  "company_name": "Unilever PLC",
  "isin": "GB00B10RZP78",
  "cusip": null,
  "sedol": null,
  "listing_currency": "EUR",
  "country": null,                 # new optional field — see §Country below
  "asset_class": "Equity",
  "provider_symbols": {"yfinance": "UNA.AS", "tradingview": "EURONEXT-UNA"},

  # Effective resolved values (computed, never stored, always
  # recomputed via the existing resolvers — read-only display)
  "effective_yfinance_symbol": "UNA.AS",
  "effective_tradingview_symbol": "EURONEXT-UNA",

  "status": "ACTIVE",
  "updated_at": "2026-09-07T...",
  "_etag": "\"...\""                # for optimistic concurrency on PATCH
}
```

### Read-only vs. editable

- **Read-only, never editable via this form**: `security_id`,
  `exchange_mic`, `ticker` — identity fields. Changing any of these is an
  identity migration (per the PEP/AD repair pattern), never a form edit,
  because ledger/config/options-chain references are keyed on
  `security_id` and MIC-driven eligibility/enrichment behavior; a silent
  form edit could desync those without the backup/collision/holdings-
  equivalence safeguards those scripts provide.
- **Editable metadata**: `company_name`, `isin`, `cusip`, `sedol`,
  `listing_currency`, `country` (new, optional), `asset_class`,
  `provider_symbols.{yfinance,tradingview}`.
- **`country`**: does not exist in the current `security_master` schema
  at all. Treat as a new, purely additive, optional field — absent on
  existing docs (renders as "Not set" in the UI, never defaulted/guessed),
  written only when the user explicitly sets it. No backfill, no
  migration triggered by its introduction.

### Validation / collision protections for the editable fields

- `provider_symbols`: **must** go through `validate_provider_symbols()`
  (reused) before merge-write — malformed key/value/oversized map is
  rejected with 422, not silently dropped or written raw.
- `isin`/`cusip`/`sedol`: if changed to a non-empty value, run the exact
  same collision check `create_security` already performs for `isin`
  (`_find_by_isin`, cross-partition query) — reused, generalized to also
  cover `cusip`/`sedot` collision if those fields gain an equivalent
  lookup (if no existing lookup helper exists for cusip/sedol, this
  section's PATCH must add one narrowly-scoped one mirroring
  `_find_by_isin`, not invent a divergent collision strategy). Abort with
  409 + existing colliding doc reference on any hit — never silently
  overwrite another security's hard identifier.
- `listing_currency`: editable, but changing it does **not** re-derive or
  touch any ledger `gross.currency` (per the hard-won PEP lesson — ledger
  currency fields are never influenced by or inputs to this field, and
  vice versa). No live-provider verification is required for a manual
  metadata edit through this UI (that mandatory verification requirement
  is specific to the automated repair scripts correcting a *known-wrong*
  value under production-incident review — this is a normal user-driven
  metadata edit with standard input validation only, ISO 4217 3-letter
  code format check).
- `company_name`/`asset_class`: free-text/enum-validated, no collision
  concern.
- All edits require the current `_etag` to be sent back
  (`If-Match`-equivalent `match_condition=IfNotModified`); a stale
  write returns 409 with the current server-side document so the UI can
  refresh and let the user retry — reusing the established
  `MatchConditions.IfNotModified` pattern already present elsewhere in
  `cosmos_db.py`, not a new concurrency mechanism.

## New endpoint

```
PATCH /api/symbols/{symbol}/security
Body: {
  "_etag": "\"...\"",              # required — optimistic concurrency
  "company_name"?: str,
  "isin"?: str | null,
  "cusip"?: str | null,
  "sedol"?: str | null,
  "listing_currency"?: str,        # ISO 4217, validated format only
  "country"?: str | null,
  "asset_class"?: str,
  "provider_symbols"?: {"yfinance"?: str, "tradingview"?: str, ...}
}
```

- 200 with the updated `security` projection (including recomputed
  `effective_*_symbol` fields and new `_etag`) on success.
- 404 if the symbol/security doesn't exist.
- 409 on ETag mismatch (concurrent edit) or ISIN/CUSIP/SEDOL collision —
  distinguishable by an `error` discriminator
  (`"etag_conflict"` vs `"collision"`), body includes current/colliding
  doc for the frontend to reconcile.
- 422 on `validate_provider_symbols()` rejection or malformed
  `listing_currency`/enum value.
- **Never** accepts `security_id`, `exchange_mic`, or `ticker` in the
  body — any attempt to pass them is a 400 ("identity fields are
  read-only; use the migration workflow").
- Transaction boundary: single-document `replace_item` on the
  `security_master` doc with `match_condition`. No cross-document writes
  in this endpoint (unlike the identity-repair scripts) — `symbol_config`,
  `ledger_txn`, and enrichment/options_chain docs are untouched by this
  PATCH, since none of the editable fields require repointing anything.

## `symbol_config` toggles/alerts/notifications (existing `PUT`, reused)

- The Symbol Configuration card also surfaces (and lets the user edit)
  `watchlist.{covered_call,cash_secured_put,buy_tracker}` and
  `telegram_notifications_enabled` — via the **existing**
  `PUT /api/symbols/{symbol}` endpoint and its existing
  `enforce_us_options_eligible` guard (§J.4.3). No new endpoint, no new
  eligibility logic.
- For a non-XNYS/XNAS security (e.g. post-AD-repair `XAMS:AD`), these
  toggles render **visibly disabled** (not hidden — the user should see
  they exist and why they're unavailable) with the same 403 rationale
  message the backend already returns, sourced from the existing
  `us_options_eligible` field already present in the detail response —
  no duplicate frontend eligibility computation.

## Enrichment state display

- Surfaced read-only from the existing `enrichment` field already in the
  detail response: `last_updated`, `quality_score`, `category`,
  `entry_tag`, `momentum`, plus the **effective provider symbol actually
  used** (`effective_yfinance_symbol`, computed identically to the
  enrichment job's own `resolve_yfinance_symbol` call — same function,
  same precedence, so the displayed "what enrichment will fetch" can
  never drift from what it actually fetches).
- Error state: if `enrich_symbol` last failed, `portfolio_enrichment.py`
  today only logs a warning and returns `None` — **no error is currently
  persisted onto the symbol_config document**. This design does not
  invent a new persisted-error field (that would require a
  `portfolio_enrichment.py` change, out of scope/authorized paths below);
  instead, the "explicit rerun action" (§ below) surfaces its own
  synchronous result (success/failure + message) directly in the API
  response of that action, which is sufficient for the user-triggered
  case without touching the scheduled-job code path.
- **Explicit rerun action**: `POST /api/symbols/{symbol}/enrichment/refresh`
  (new, thin endpoint) — synchronously calls the existing
  `enrich_symbol(ticker, yf_symbol=resolve_yfinance_symbol(...))` and
  `cosmos.update_symbol_enrichment(...)` (both reused verbatim from
  `portfolio_enrichment.py`, no reimplementation), returns
  `{"status": "ok", "enrichment": {...}}` on success or
  `{"status": "error", "detail": "..."}` (200, not 500, since a fetch
  failure is an expected/handled outcome, not a server error) on failure.
  Must reuse the exact same US-eligibility-agnostic behavior enrichment
  already has today (enrichment runs for **any** MIC with a resolvable
  provider symbol — it is not gated by `us_options_eligible`, which only
  gates the Options section/toggles, a distinction already correctly
  drawn elsewhere in this codebase and preserved here).

## Audit/history

- No dedicated change-history document type exists today for
  `security_master`. Given the small edit surface (metadata + provider
  overrides only, no identity change), a full audit-log store is out of
  scope for this iteration; the endpoint's response includes
  `updated_at` (already a standard field) so the UI can show "last
  edited" — sufficient for this feature's risk level. If deeper audit
  history is later required, that is a separate, explicitly-scoped
  follow-up, not silently bundled here.

## Placement / responsive / accessibility

- Renders as its own card (`SymbolConfigurationCard`) inside the existing
  `Stocks` `DetailSection`, positioned immediately before
  `PortfolioHoldingsCard`. Uses the same card/section styling conventions
  as `PortfolioHoldingsCard`/`StockTransactionsTable` for visual
  consistency (no new design-system pattern).
- Read-only fields render as plain labeled text; editable fields render as
  a form with a single explicit "Save" action (not autosave-on-blur, to
  avoid partial/accidental writes given the collision/ETag semantics) and
  inline validation errors mapped from the 409/422 responses above.
- Must be keyboard-navigable and use accessible form labels/`aria-*`
  attributes consistent with existing form components in this codebase
  (e.g. `AddMovementDialog`'s existing patterns) — reused conventions, not
  a new accessibility approach.
- Collapsible/responsive at the same breakpoints already used by
  `DetailSection`/`PortfolioHoldingsCard` — no new breakpoint system.

## No duplicate Add Symbol/Add Security experience

- This section edits an **existing** security's metadata only — it must
  never expose a "create new security" path, ticker/MIC entry, or
  duplicate any part of the single canonical Add Security flow
  (`danny-single-add-symbol-contract.md`). If the user needs to correct
  `security_id`/`exchange_mic`/`ticker`, the UI must point them to
  contacting an operator for the migration/repair workflow (as already
  established for PEP/AD), not offer an inline "fix" control.

## Assignment

- **Backend**: Livingston — `CosmosSecuritiesService.update_security()`
  (new, ETag-guarded, ISIN/CUSIP/SEDOL collision-checked, provider_symbols
  merge via `validate_provider_symbols`), `PATCH
  /api/symbols/{symbol}/security` endpoint, `POST
  /api/symbols/{symbol}/enrichment/refresh` endpoint, and extending the
  `security` projection in `_compute_symbol_detail` with the new fields
  (`provider_symbols`, `cusip`, `sedol`, `country`, `asset_class`,
  `effective_yfinance_symbol`, `effective_tradingview_symbol`, `_etag`).
- **Frontend**: Rusty — `SymbolConfigurationCard` component, wired into
  `frontend/src/app/symbols/[symbol]/page.tsx` immediately above
  `PortfolioHoldingsCard` inside the existing `Stocks` section guard;
  reuses existing `us_options_eligible`/`tradingview_symbol` fields for
  the toggle-disable and effective-symbol display, no new frontend
  eligibility logic.
- **Tests**: Basher — backend: ETag conflict, ISIN/CUSIP/SEDOL collision,
  `validate_provider_symbols` rejection paths, identity-field-rejection
  (400 on `security_id`/`exchange_mic`/`ticker` in body), enrichment
  refresh success/failure response shapes, `us_options_eligible` toggle
  guard reuse (no regression on existing `PUT` behavior); frontend:
  rendering of read-only vs. editable fields, disabled-toggle messaging
  for non-US MICs, save/error-state handling, placement before
  `PortfolioHoldingsCard`.

## Authorized paths

- `backend/src/portfolio/cosmos_securities.py` (add `update_security`)
- `backend/web/app.py` (new `PATCH .../security` and `POST
  .../enrichment/refresh` endpoints; extend `_compute_symbol_detail`'s
  `security_field` projection)
- `backend/tests/test_symbol_configuration_section.py` (new, Basher)
- `frontend/src/components/SymbolConfigurationCard.tsx` (new, Rusty)
- `frontend/src/app/symbols/[symbol]/page.tsx` (wire in the new card)
- `frontend/src/app/api/symbols/[symbol]/...` BFF routes as needed to
  proxy the two new backend endpoints (Rusty)
- No changes authorized to `provider_symbols.py`,
  `us_exchange_eligibility.py`, `portfolio_enrichment.py`'s scheduled-job
  logic, or the Add Security/Add Symbol flow — this feature only
  **consumes** those existing contracts.

Do not implement beyond this authorization; no production writes.
