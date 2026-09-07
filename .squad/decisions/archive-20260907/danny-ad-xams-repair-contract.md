# Danny — Design Review: AD (Ahold Delhaize) XNYS→XAMS Security Identity Repair

## Verdict: APPROVED (design only — no implementation, no production writes)

## Source facts (read-only audit, not re-verified by Danny beyond code inspection)

- `sec_XNYS_AD`: `security_id="XNYS:AD"`, `exchange_mic="XNYS"`,
  `listing_currency="EUR"`, `provider_symbols.yfinance="AD"` (bare, no
  suffix — consistent with the wrong XNYS assignment).
- `config_AD`: `security_id="XNYS:AD"`, `exchange="XNYS"`.
- 14 `ledger_txn` reference `XNYS:AD` (3 BUY, 11 DIVIDEND).
- 4 `optchain_AD_*` shards exist in the symbols container — these should
  never have been generated once eligibility is correctly XAMS.
- `enrichment_history`/calendar (earnings/ex-dividend) docs are
  **symbol-keyed only** (`id`/partition = ticker `"AD"`, no MIC/security_id
  embedded) — confirmed by inspecting `options_chain_store.py` and
  `cosmos_db.py::get_enrichment_history` (`doc_type="enrichment_history"`,
  keyed purely by `symbol`). These require **no repointing** — they remain
  correctly attached to ticker `AD` regardless of MIC correction.
- Correct identity: `XAMS:AD` (Euronext Amsterdam). Expected providers:
  Yahoo `AD.AS` (per the already-approved `MIC_TO_YFINANCE_SUFFIX["XAMS"]
  = ".AS"` in `provider_symbols.py`), TradingView `EURONEXT-AD` (per the
  already-approved `danny-tradingview-symbol-contract.md` mapping).

## This is genuinely a distinct repair pattern, not a copy of PEP

Two material differences from the PEP repair disqualify a literal reuse of
`_resolve_provider_mic`/`EXCHANGE_MAP`:

1. **`dgi_screener.EXCHANGE_MAP` and `LEGACY_ALIAS_TO_MIC` are US-only**
   (`NYQ/NMS/NGM/NCM/NIM/PCX/ASE/BTS/YHD` → `NYSE/NASDAQ/AMEX`; alias table
   only has `NYSE/NASDAQ/AMEX`). Neither contains anything for Amsterdam.
   Reusing them as-is for XAMS corroboration is impossible — there is
   nothing to reuse, and inventing a guessed raw-exchange-code mapping
   would violate "do not guess."
2. **PEP was a currency correction on an already-XNAS-shaped identity**;
   AD is a **cross-border MIC correction that flips US-options-eligible
   → ineligible**, which additionally requires purging options-only
   artifacts and re-validating config toggles against
   `us_exchange_eligibility.py` — PEP never touched that surface.

## Required design: `backend/scripts/repair_ad_xams_security_id.py`

### 1. Live provider verification (mandatory, evidence-based, not guessed)

- Query `YFinanceFetcher('AD.AS').get_ticker_data()` (the literal suffixed
  symbol constructed via `suggest_yfinance_symbol("AD", "XAMS")` —
  i.e. the script must call the **existing** `provider_symbols` helper to
  build the query symbol, never hardcode `"AD.AS"` as a bare string).
- Corroborate against a **new, explicitly-scoped, minimal international
  exchange-name hint table** — e.g.
  `_INTERNATIONAL_MIC_NAME_HINTS: Dict[str, tuple[str, ...]]` — populated
  **only** with the literal `fullExchangeName`/`exchange` substrings
  Livingston actually observes from the live `AD.AS` call (recorded in the
  audit report and in a code comment citing the live observation, exactly
  as `dgi_screener.EXCHANGE_MAP`'s existing entries were themselves built
  from real observed yfinance data — not invented). Do not backfill
  guessed entries for XPAR/XETR/XBRU/XLIS; add them only when each is
  independently verified against a real security on that exchange (mirrors
  the caution already noted in `danny-tradingview-symbol-contract.md`).
- This hint table is **exchange-name corroboration only** — it must never
  duplicate or diverge from `MIC_TO_YFINANCE_SUFFIX` (the sole authority
  for suffix generation) or `LEGACY_ALIAS_TO_MIC` (the sole authority for
  legacy US alias resolution). It is additive, not a competing table.
- Currency: verify provider `currency == financialCurrency == "EUR"`
  (matching the already-correct `sec_XNYS_AD.listing_currency`). If the
  provider disagrees, treat exactly like the PEP correction: **no currency
  change without corroborating live evidence**; report the discrepancy and
  abort the currency portion (exit 2 if `--listing-currency` is explicitly
  passed and unverifiable) rather than guessing.
- Any unreachable/mismatched provider response **aborts before backup or
  any mutation**, exit code 2 — identical fail-closed contract to the PEP
  repair.

### 2. Backup (mandatory, before any write)

- Cover **every** document type touched: `sec_XNYS_AD` (source),
  `config_AD`, all 14 `ledger_txn` docs, all 4 `optchain_AD_*` shards
  slated for deletion, and any `import_session` docs referencing
  `XNYS:AD`. Same conventions as the PEP repair: full document bodies
  (including `_etag`), SHA-256 checksum, `generated_at` timestamp,
  separate `--restore` mode, resumability/idempotency, exit codes
  0/2/3 matching the established convention.
- `enrichment_history`/calendar docs are explicitly **out of scope for
  mutation** (they need no repoint), but the script must still record in
  its audit report that they were inspected and found symbol-only/
  unaffected — do not silently skip verifying this per-run.

### 3. Collision + target creation

- Check for an existing `sec_XAMS_AD` before creating — abort (exit 2,
  before backup) on any conflicting hard identifier (ISIN/CUSIP/SEDOL),
  identical to PEP's collision gate.
- Create `sec_XAMS_AD` with `security_id="XAMS:AD"`,
  `exchange_mic="XAMS"`, `listing_currency` preserved as `"EUR"` unless
  provider-corroborated otherwise (see §1), `provider_symbols.yfinance`
  corrected to `"AD.AS"` (never left bare), all other fields copied
  byte-identical from the source (`isin`/`cusip`/`sedol`/`aliases`/
  `asset_class`/`broker_ids`/`created_at`), `updated_at`/audit metadata
  refreshed.

### 4. Ledger repoint

- All 14 `ledger_txn` docs: `security_id` field only changes
  `"XNYS:AD" → "XAMS:AD"`. Movement IDs, account IDs (partition keys),
  `gross`/`fees`/`net`/`fx`/`quantity`/`trade_date`/`withholding` remain
  byte-identical — same invariant as PEP's PEP-11 class.
- Holdings-equivalence check before/after (movement count + net share
  quantity per account) — abort before delete if mismatched, same as
  PEP's PEP-10.

### 5. `config_AD` repoint + eligibility re-validation (new vs. PEP)

- `security_id` → `"XAMS:AD"`, `exchange` → `"XAMS"`.
- **Watchlist option toggles and notifications must be forced to
  disabled** (`watchlist.covered_call`, `watchlist.cash_secured_put`,
  `watchlist.buy_tracker`, `telegram_notifications_enabled` — all
  `False`) **if any are currently `True`**, and this must be logged as an
  explicit repair action in the report (not silently applied). This is
  not a new policy invention — it is the direct, mandatory consequence of
  already-approved `us_exchange_eligibility.enforce_us_options_eligible`
  (§J.4.3), which the live `PUT /api/symbols/{symbol}` endpoint already
  enforces going forward (any attempt to re-enable these toggles for a
  non-XNYS/XNAS symbol is already blocked with 403). A `config_AD` left
  with these toggles `True` under an XAMS identity would be an
  inconsistent state the running system could never have produced itself
  post-correction, and per current policy must not persist through the
  repair. `display_name`, `total_shares`, `positions`, `created_at`, and
  any other unrelated fields must be preserved byte-identical.
- If all toggles are already `False` (the common case, since AD was never
  legitimately options-eligible even under the wrong XNYS label unless a
  user actually enabled them while it was mislabeled), this step is a
  documented no-op.

### 6. Options-chain artifact purge (new vs. PEP)

- All 4 `optchain_AD_*` shards must be **deleted**, not repointed —
  they are entirely invalid artifacts of the wrong eligibility state and
  have no correct XAMS equivalent to migrate to (Amsterdam has no
  options-eligible representation in this system). Deletion happens only
  **after** backup and only after the config/ledger/security_master writes
  have succeeded (final step, mirroring PEP's "delete source only after
  verification" ordering — here it's "delete stale artifacts only after
  the identity that produced them is corrected").
- Restore must recreate these shards exactly as backed up (for symmetry/
  reversibility), even though a fresh `--apply` would never regenerate
  them under the corrected identity.

### 7. Source deletion + write ordering

Discovery → collision check → provider verification (currency +
exchange, abort-before-backup on failure) → **backup** → create
`sec_XAMS_AD` → repoint 14 ledger_txn → repoint `config_AD` (+ force
toggles off, logged) → repoint any `import_session` refs → holdings
equivalence re-check → delete 4 `optchain_AD_*` shards → delete
`sec_XNYS_AD` (only if zero remaining references) → post-apply
verification/reconciliation counts.

### 8. Idempotency / resumability / exit codes

Identical convention to the PEP repair: re-running `--apply` after partial
or full success must be a safe no-op or safely resume; `--restore` is a
separate command; exit codes 0 (success/no-op), 1 (bad args), 2 (backup
failure / collision / provider verification failure — no writes), 3
(post-apply verification failure — source never deleted).

## Tests (independent authorship required)

Cover, at minimum: dry-run zero writes; provider-corroborated MIC/currency
happy path; provider mismatch/unreachable abort-before-backup for both
currency and exchange corroboration; collision abort; ledger byte-
equivalence (PEP-11-style); holdings-equivalence gate; config toggle
force-disable when previously `True` (and no-op when already `False`),
with an explicit assertion that this is logged; options_chain shard
deletion only after successful repoint, never before backup; restore
recreates deleted options_chain shards; idempotent re-run; safe CLI
defaults (fails closed without Cosmos env vars); no repoint attempted on
`enrichment_history`/calendar docs (regression guard proving they are
correctly left untouched, not accidentally repointed to a security_id they
never had).

## Assignment

- **Implementation**: Livingston — original author of the PEP repair
  pattern this generalizes; not locked out here since this is a distinct
  new artifact (`repair_ad_xams_security_id.py`), no prior rejected work
  on it exists.
- **Tests**: Basher — independent of Livingston, no prior authorship on
  this artifact either.
- Both must treat `.squad/decisions/inbox/danny-ad-xams-repair-contract.md`
  (this file) as authoritative; any live-provider-observed corroboration
  strings must be cited in code comments and in the audit report, not
  invented.

## Authorized paths

- `backend/scripts/repair_ad_xams_security_id.py` (new, Livingston)
- `backend/tests/test_repair_ad_xams_security_id.py` (new, Basher)
- No changes authorized to `provider_symbols.py`, `dgi_screener.py`,
  `us_exchange_eligibility.py`, `watchlist_membership.py`, or any other
  shared module — this repair only **consumes** those existing contracts.

No production execution is authorized under this review.
