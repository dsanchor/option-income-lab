### 2026-09-07T13:43:00+02:00: Danny — Design Review Decision
**Request:** Assess whether pre-SecurityMaster `symbol_config` documents need migration for homogeneous Cosmos data; if beneficial, design (not implement/run) a safe, backed-up, restorable migration tool.
**Skill applied:** `.squad/skills/cosmosdb-migration/SKILL.md` (offline batch, backup-first, four-phase, idempotent, dry-run-default).

---

## 1. Recommendation

**Migration is recommended — narrowly scoped to two structural fixes only.** One of the two fixes closes a genuine, currently-live bug (not just a cosmetic inconsistency): legacy free-text `exchange` values silently break Amendment J's options-eligibility check today.

## 2. Findings

**Schema/identity facts (confirmed by inspection):**
- `symbol_config` doc: `id = config_{TICKER}`, partition key = `symbol` = `{TICKER}` (`cosmos_db.py:143`, `symbol_config_sync.py`).
- `security_master` doc: `id = sec_{MIC}_{TICKER}`, partition key = `symbol` = `{TICKER}` (`cosmos_securities.py:32-40`) — **co-located in the same partition** as its `symbol_config`, but a distinct document. A single ticker partition can legitimately hold **more than one** `security_master` doc (same ticker listed on different exchanges) but only **one** `symbol_config` doc — this asymmetry is the root of the one genuine ambiguity case below.
- Canonical linkage: `symbol_config.security_id` (added by `ensure_symbol_config`) points at the `security_master` doc. Legacy (pre-SecurityMaster) configs have no `security_id` at all.
- Legacy `exchange` values: the removed-per-`danny-single-add-symbol-contract.md` `POST /api/symbols` path (and any hand-created data predating it) wrote free text — `"NYSE"`, `"NASDAQ"`, `"AMEX"` — into `symbol_config.exchange`, never a MIC. `ensure_symbol_config` (canonical path) always writes a real MIC there instead (`exchange_mic` from the linked security).
- **Live bug, not just cosmetic:** `us_exchange_eligibility.py::enforce_us_options_eligible()`'s fallback (`doc.exchange`, step 2 of its 3-step MIC resolution) compares this value directly against `{"XNYS","XNAS"}` with **no legacy-alias translation** — unlike `provider_symbols.py::resolve_yfinance_symbol()`, which already has a `_LEGACY_US_EXCHANGE_ALIASES` runtime-compat shim for Yahoo resolution only. A legacy config with `exchange="NASDAQ"` therefore gets a **403 `options_not_eligible`** on every option action today, even though it is a genuine, currently-eligible NASDAQ holding. Options-screener-universe inclusion (`danny-options-screener-universe-contract.md`, also fail-closed on `doc.exchange`) has the identical exposure. This is a real defect for any real legacy holding still carrying the free-text form.
- **Genuinely optional, no migration needed (verified against runtime code, not assumed):**
  - `total_shares` on `symbol_config` — legacy/manual field, already **not** the source of truth anywhere it matters (Unified Watchlist and the Options Screener universe both use `HoldingsService.compute_holdings()`, never this field, for real share counts). Migrating it risks silently overwriting a value some other display path still reads. **Audit/report divergence only; never write.**
  - `provider_symbols` map — additive/optional per-security override; its absence is the normal, fully-supported default path for every resolver (`resolve_yfinance_symbol`, `resolve_tradingview_symbol`) — not an inconsistency.
  - `telegram_notifications_enabled` / `watchlist.{covered_call,cash_secured_put,buy_tracker}` — these are **user-set behavioral toggles**, some legacy docs default `telegram_notifications_enabled=True` (the old `cosmos_db.create_symbol` default) which may be an intentionally-relied-upon live setting. The single-Add-Symbol "all disabled by default" invariant governs **new** config creation only (already contracted) — it does **not** authorize retroactively flipping an existing user's explicit configuration. **Never touched by this migration**, full stop.

## 3. Contract — migration tool design

### 3.1 Scope of writes (exactly two, both structural/identity-only)
For each `symbol_config` document:
1. **Normalize `exchange`** from a recognized legacy alias to its MIC, using one single reused table (no new duplicate table): add `LEGACY_ALIAS_TO_MIC = {"NYSE": "XNYS", "NASDAQ": "XNAS", "AMEX": "XASE"}` to `backend/src/portfolio/provider_symbols.py`, keyed off the *same* `_LEGACY_US_EXCHANGE_ALIASES` frozenset already defined there (import, don't re-derive). Only overwrite when `doc.exchange` is exactly one of these three strings (case-normalized) — never touch a value that already looks like a MIC (5-char structure not required; simplest safe check: value already appears as a key in `MIC_TO_YFINANCE_SUFFIX` or matches `US_OPTIONS_ELIGIBLE_MICS`) and never touch anything else (fail-closed: unrecognized text is reported, not guessed).
   - Note (informational, not a defect to fix here): `AMEX → XASE` is **not** in Amendment J's `US_OPTIONS_ELIGIBLE_MICS` (`{XNYS, XNAS}`). Any real AMEX-labeled legacy doc will, after migration, correctly and honestly report as options-ineligible (a true NYSE American listing is a distinct exchange) rather than ambiguously mismatching free text — this is a correct outcome, not scope creep into Amendment J's eligible set.
2. **Link or create `security_master`** for the resulting MIC, with strict fail-closed collision handling:
   - MIC resolved this run (from step 1, or already-valid pre-existing MIC) + ticker → does a `security_master` doc already exist at exactly `sec_{MIC}_{TICKER}`? **Link** (`symbol_config.security_id = "{MIC}:{TICKER}"`), security_master untouched.
   - No exact match, but `list_securities()` (existing method, already used identically by `_compute_symbol_detail`'s ambiguity check — no new query pattern) finds **one or more** `security_master` docs for the same ticker under a **different** MIC → **fail-closed: flag `collision_ambiguous`, write nothing for this ticker.** This is the directive's explicit "no destructive guessing for duplicate tickers across exchanges" case.
   - No `security_master` exists for this ticker under *any* MIC → **create one**: `create_security({"ticker": TICKER, "exchange_mic": MIC, "company_name": symbol_config.display_name or TICKER})` (reuses `CosmosSecuritiesService.create_security` verbatim — same ISIN/security_id collision guard already built into it applies). Optional fields (`isin`, `listing_currency`, etc.) are left absent rather than fabricated. Then link.
   - Unrecognized/un-normalizable `exchange` (step 1 didn't resolve a MIC) → **flag `unresolved_exchange`, skip entirely** (no security_master lookup/creation attempted).
   - `total_shares`, `watchlist.*`, `telegram_notifications_enabled`, `display_name`, `positions`, all other fields → **never written** by this tool.

### 3.2 CLI structure (mirrors `backend/scripts/repair_options_chain_shards.py` — same team convention, same exit-code policy)
New file: `backend/scripts/migrate_legacy_symbol_config.py`
```
python -m scripts.migrate_legacy_symbol_config --audit                  # default; read-only, writes report only
python -m scripts.migrate_legacy_symbol_config --backup-only            # backup, no analysis writes needed beyond the backup file itself
python -m scripts.migrate_legacy_symbol_config --apply                  # backup (mandatory, automatic) -> transform -> write -> verify
python -m scripts.migrate_legacy_symbol_config --restore <backup_file>  # separate mode; never combinable with --apply
```
- **`--apply` unconditionally performs a fresh backup first** (§3.3) — there is no way to run a write pass without one; this is enforced in code, not just documented.
- **`--audit` is the default when no mode flag is given** — matches the skill's "dry-run flag, always test before executing" pattern and this team's existing `--apply`-gated convention.
- Exit codes: `0` = normal run (including "nothing to migrate" and "N flagged for manual review" — flags are reported outcomes, not script failures); `1` = bad CLI arguments/mutually-exclusive flags; `2` = backup step failed (never proceeds to write); `3` = post-migration verification failed (§3.6) — non-zero specifically so CI/an operator cannot miss a verification regression.

### 3.3 Backup (mandatory, pre-write, complete)
Before any write, dump **every** `symbol_config` document (the full affected set — all docs are candidates for at least a read/classify pass) plus every `security_master` document already present (needed for accurate restore of the "before" graph, since new ones may be created), to a single JSON file:
```
backend/scripts/migration_backups/symbol_config_migration_{utc_timestamp}.json
```
containing, per document: `id`, partition key (`symbol`), full document body (Cosmos system keys `_rid/_self/_attachments/_ts` stripped, but **`_etag` preserved separately** as its own field — needed for the CAS-gated writes in §3.4 and for restore integrity checking), plus a top-level `generated_at` timestamp and a `sha256` checksum computed over the serialized document list (detects any accidental hand-edit of the backup file before a restore). `migration_backups/` is added to `.gitignore` (backups are operational artifacts, not source).

### 3.4 Write safety
Every write (both the `exchange` normalization and the `security_master` create-or-link) is ETag-gated compare-and-swap, identical pattern to `repair_options_chain_shards.py::repair_shard` — if a document changed concurrently since the backup snapshot, that document's update is skipped and reported as a `cas_conflict`, never blindly overwritten. New `security_master` document creation reuses `create_security`'s own collision guard (id/ISIN), so a race with an independent concurrent Add-Symbol create is caught there, not silently duplicated.

### 3.5 Idempotency / resumability
Both the classification pass and the write pass are naturally idempotent: a `symbol_config` whose `exchange` is already a valid MIC and already has a matching `security_id` is classified `already_canonical` and produces zero writes on any re-run. A partial run (interrupted mid-way) can simply be re-invoked with `--apply` again — already-migrated docs are skipped (not re-written), and a fresh backup is taken again on the resumed run (cheap, and guarantees the resumed run's own backup reflects the correct current state rather than relying on a stale earlier one).

### 3.6 Report (machine-readable) + post-migration verification
`--audit` and `--apply` both emit a JSON report (dataclass, mirroring `RepairReport`'s shape) with: `total_configs_scanned`, `already_canonical`, `normalized_exchange_count`, `security_master_linked_count`, `security_master_created_count`, `collision_ambiguous` (list of ticker + candidate security_ids), `unresolved_exchange` (list of ticker + raw exchange value), `cas_conflicts`, `errors`. After `--apply` writes, a **post-flight verification pass** re-reads every document this run touched and asserts: (a) no `symbol_config` this run wrote still has a non-MIC `exchange` value that was supposed to be normalized; (b) every `security_id` this run set resolves to a `security_master` doc that actually exists; (c) reconciliation counts match — `normalized_exchange_count + already_canonical + collision_ambiguous + unresolved_exchange == total_configs_scanned`. Any mismatch is a hard failure (exit code 3, §3.2), never a silently-logged warning.

### 3.7 Restore (separate command/mode from backup)
`--restore <backup_file>` is a **distinct** invocation, never combinable with `--apply`/`--audit`/`--backup-only` in the same run. It re-reads the backup JSON, verifies its checksum first (abort with a clear error if it doesn't match — the file may have been altered), and for each backed-up document does an ETag-gated `replace_item` back to the exact pre-migration body if the doc still exists with the id the backup recorded, or `create_item` if the migration had created a new `security_master` doc that restore must now delete instead (detected via a `created_by_migration: true` marker this tool adds only to its own newly-created `security_master` docs, never to pre-existing ones) — i.e. restore both reverts mutated fields and removes migration-created documents, returning Cosmos to exactly the pre-migration snapshot.

### 3.8 Explicitly out of scope for this tool (per §2)
No writes to `total_shares`, `watchlist.*`, `telegram_notifications_enabled`, `display_name`, `positions`, `provider_symbols`, or any `security_master` field on an already-existing security. No auto-resolution of `collision_ambiguous`/`unresolved_exchange` cases — these are always reported for manual, human-driven follow-up, never guessed.

## 4. Tests (fake Cosmos containers — no real Cosmos access)
New `backend/tests/test_migrate_legacy_symbol_config.py`, using an in-memory `FakeSymbolsContainer` (matching this repo's existing `FakePortfolioContainer` convention — dict-backed `create_item`/`read_item`/`replace_item`/`query_items`, with etag bump on write for CAS testing) covering:
- Already-canonical config → zero writes, classified `already_canonical`.
- Legacy `"NASDAQ"`/`"NYSE"`/`"AMEX"` alias → normalized to `XNAS`/`XNYS`/`XASE`, linked to an existing matching `security_master` when present.
- No existing `security_master` for the ticker → one is created with the migration marker, then linked.
- Ticker with an existing `security_master` under a **different** MIC → `collision_ambiguous`, zero writes for that ticker.
- Unrecognized `exchange` text (e.g. `"OTC"`, empty string) → `unresolved_exchange`, zero writes.
- CAS conflict simulation (etag changed between backup snapshot and write) → reported, not overwritten.
- `--audit` mode never calls any write method on the fake container (assert zero `create_item`/`replace_item` calls).
- Backup file: correct checksum, correct `_etag` capture, restore round-trip test (apply → restore → state equals original backup contents byte-for-byte on the fields that matter).
- Idempotent re-run: running `--apply` twice produces zero additional writes the second time.
- Exit code assertions for each mode (0 normal/flagged, 1 bad args, 2 backup failure simulated, 3 verification-mismatch simulated).

## 5. Assignments

- **Livingston** — Implement `backend/scripts/migrate_legacy_symbol_config.py` (audit/backup/apply/restore modes, `LEGACY_ALIAS_TO_MIC` addition to `provider_symbols.py`) per §3. Owns Cosmos persistence domain (consistent with prior assignments for hard-delete/backfill work). **Do not run against production** — this design authorizes implementation and local/fake-container testing only.
- **Basher** — `backend/tests/test_migrate_legacy_symbol_config.py` per §4.

## 6. Authorized files
- `backend/scripts/migrate_legacy_symbol_config.py` (new)
- `backend/src/portfolio/provider_symbols.py` (additive: `LEGACY_ALIAS_TO_MIC` only — no changes to existing tables/functions)
- `backend/tests/test_migrate_legacy_symbol_config.py` (new)
- `.gitignore` (add `backend/scripts/migration_backups/`)

No other files. No production execution. No changes to `us_exchange_eligibility.py`'s eligible-MIC set (out of scope, owned by Amendment J).

## 7. Verdict

**APPROVED** — migration recommended, narrowly scoped per §3. No code written or executed by Danny.
