# Danny — Design Review: PEP Security Identity Repair (NNYS:PEP → XNAS:PEP)

## Status: APPROVED (design only — no writes performed)

## 1. Problem statement (verified read-only facts)

- `symbols` container, `config_PEP` (symbol_config, id=`config_PEP`,
  partition key=`PEP`): `exchange="NASDAQ"` (legacy free-text field),
  **no `security_id` set** — never linked to a canonical identity.
- `symbols` container, `sec_NNYS_PEP` (security_master, id=`sec_NNYS_PEP`,
  partition key=`PEP`): `exchange_mic="NNYS"` (**not a valid ISO 10383
  MIC** — not in `MIC_TO_YFINANCE_SUFFIX`, `US_OPTIONS_ELIGIBLE_MICS`, or
  `LEGACY_ALIAS_TO_MIC.values()` — confirmed invalid by the same
  `_KNOWN_MICS` check already used in
  `migrate_legacy_symbol_config.py`), `security_id="NNYS:PEP"`,
  `listing_currency="EUR"`.
- `portfolio` container: numerous `ledger_txn` docs (partition key=
  `account_id`, doc id=`movement_id`) with `security_id="NNYS:PEP"`.
- The approved legacy migration (`danny-legacy-symbol-config-migration-
  contract.md`) correctly left this alone: `config_PEP.exchange="NASDAQ"`
  is a valid, resolvable legacy alias, but the migration's scope was
  linking *unlinked* configs to an *existing, valid* security_master —
  here the only existing security_master for PEP is corrupt
  (`exchange_mic="NNYS"` fails `_KNOWN_MICS`), so the tool correctly
  refused to link `config_PEP` to it rather than guess. This is a
  distinct, narrower repair: fixing a single already-corrupt identity,
  not linking an unlinked config to a valid one.

## 2. Evidence-based derivation (no invented assumptions)

**MIC.** `config_PEP.exchange="NASDAQ"` is itself the authoritative,
already-stored, unambiguous evidence — not a new assumption. It resolves
via the existing, already-approved `LEGACY_ALIAS_TO_MIC` table
(`provider_symbols.py`) to `XNAS`. `"NNYS"` on the security_master side is
provably invalid (fails `_KNOWN_MICS`), so precedence goes to the
well-formed `config_PEP.exchange` field over the malformed
`security_master.exchange_mic`. **The script must not hardcode `"XNAS"`
as a constant** — it must derive it at runtime from
`config_PEP.exchange` via `LEGACY_ALIAS_TO_MIC`, and abort
(`collision_ambiguous`/`unresolved`) if that resolution is anything other
than a single, clean match. This keeps the one source of MIC-alias truth
in `provider_symbols.py` — no new mapping table.

**Currency.** There is no in-repo constant asserting PEP trades in USD.
The verifiable evidence lives in the referencing `ledger_txn` documents
themselves: each carries its own `gross.currency` / `fees.currency` /
`net.currency` (independent of `security_master.listing_currency`,
captured at import/entry time). **Currency correction is
evidence-gated, not assumed**: the script reads `gross.currency` off
every discovered `NNYS:PEP` ledger_txn and only proposes correcting
`listing_currency` if that value is **unanimous** across all referencing
movements and differs from the current `"EUR"`. Any split/ambiguous
result leaves `listing_currency` untouched and is reported as
`currency_evidence: inconclusive` for manual review — never guessed.

## 3. Full reference discovery (all containers/doc types)

1. `symbols` container: point-read `sec_NNYS_PEP` (partition `PEP`) and
   `config_PEP` (partition `PEP`).
2. `symbols` container: point-read candidate target
   `sec_XNAS_PEP` (= `security_id_to_doc_id(make_security_id("XNAS","PEP"))`,
   partition `PEP`) — **collision check**:
   - **Not found** → safe to create.
   - **Found, well-formed, ticker=PEP** → target already exists; do not
     create a duplicate — re-point references to it instead, and diff its
     `company_name`/`isin`/`cusip`/`sedol` against `sec_NNYS_PEP`'s; any
     conflicting non-empty field on a real identifier (isin/cusip/sedol)
     is a `collision_ambiguous` abort (never merge silently).
   - **Found but itself malformed/inconsistent** → abort, manual review.
3. `portfolio` container: cross-partition query
   `SELECT * FROM c WHERE c.doc_type='ledger_txn' AND c.security_id='NNYS:PEP'`
   — this is the complete, authoritative set of financial docs to
   re-point (partition key is `account_id`, not `security_id`, so this
   must be cross-partition; no other query shape reaches all of them).
4. `portfolio` container: cross-partition query
   `SELECT * FROM c WHERE c.doc_type='import_session'` (TTL-bound,
   likely already expired for a historical import — included for
   completeness), scanned in memory for `resolution_map` values or
   `enrolled_security_ids` entries equal to `"NNYS:PEP"`. Treated as
   best-effort/non-blocking: if none remain (expired), report
   `import_session_refs: 0 (ttl-expired or none)`.
5. Nothing else in the codebase stores `security_id` (confirmed:
   `holdings_service.py` and `import_service.py` only read/aggregate the
   field from `ledger_txn`/`import_session`, they hold no independent copy).

## 4. Backup (mandatory, before any write)

One JSON backup file (`migration_backups/pep_security_id_repair_<UTC
timestamp>.json`), covering **every** document found in §3 step 1, 3, 4
(and step 2's `sec_XNAS_PEP` if found, for restore-safety even though
it won't be deleted): `id`, `partition_key`, `_etag`, full cleaned `body`,
plus a top-level `sha256` checksum over the serialized document list and
`generated_at` timestamp — identical shape to
`migrate_legacy_symbol_config.py`'s `MigrationBackup`/`BackupEntry`
dataclasses (reused, not reinvented).

## 5. Write ordering (create-before-delete, per skill)

All writes are ETag-gated (CAS) using the etag captured in the
*discovery* pass immediately preceding each write group (re-read fresh
if a large amount of time elapses between discovery and write, per §7).

1. **Create or confirm `sec_XNAS_PEP`.** If not found (§3.2 case a):
   `create_item` a new doc = clone of `sec_NNYS_PEP`'s cleaned body with
   `id="sec_XNAS_PEP"`, `security_id="XNAS:PEP"`, `exchange_mic="XNAS"`,
   `listing_currency` set per §2's evidence-gated currency check (else
   left as `"EUR"`), `created_at` **preserved** from the original doc
   (not reset to now — this is a corrected identity, not a new
   security), `updated_at=now`, plus an audit trail field
   `migrated_from="NNYS:PEP"` and `migration_note` explaining the repair.
   Everything else (`company_name`, `isin`/`cusip`/`sedol`, `aliases`,
   `asset_class`, `broker_ids`, `provider_symbols`) copied byte-for-byte
   unchanged. If found (§3.2 case b), skip creation — no write.
2. **Patch `config_PEP.security_id = "XNAS:PEP"`.** In-place
   `replace_item` (doc id/partition key unchanged — this is a field
   patch, not an ID migration). `exchange="NASDAQ"` is left untouched
   (preserves the legacy audit field; only the missing canonical link is
   added). No agent/alert/notification/warmup fields touched.
3. **Patch every matching `ledger_txn.security_id = "XNAS:PEP"`.**
   In-place `replace_item` per doc — **`id` (movement_id) and
   `account_id` (partition key) are never touched**, and no financial
   field (`quantity`, `gross`, `fees`, `net`, `fx`, `withholding`,
   `trade_date`, `correction_status`, `ca_group_id`, etc.) is read or
   rewritten — only the single `security_id` string field changes. Each
   write is independently ETag-gated; a conflict on one movement does
   not block the others (best-effort loop, failures collected and
   reported, re-run resumes from whichever movements still show
   `"NNYS:PEP"`).
4. **Patch matching `import_session.resolution_map`/
   `enrolled_security_ids` entries** (§3.4), same in-place
   single-field-value patch, same ETag gating. Skipped entirely if none
   found (already expired).
5. **Post-write verification (must pass before step 6):**
   - Re-run the §3.3 query: zero remaining `ledger_txn` docs with
     `security_id="NNYS:PEP"`.
   - Re-run the §3.4 scan: zero remaining references.
   - **Holdings equivalence**: run
     `HoldingsService.compute_holdings()` filtered/grouped by ticker
     `PEP` both immediately *before* step 1 (captured during discovery)
     and *after* step 5's re-query, and assert `total_shares`,
     `average_cost_basis` (or equivalent aggregate fields the service
     exposes), and movement count are **identical** except the
     dictionary key changes from `"NNYS:PEP"` to `"XNAS:PEP"`. Any
     numeric mismatch aborts before step 6 with exit code 3 — the corrupt
     `sec_NNYS_PEP` doc is **never deleted** in this case, leaving the
     system in a safe (if untidy) re-pointed state for manual follow-up.
6. **Delete `sec_NNYS_PEP`** — only reached if step 5 passed. Re-read the
   doc immediately before deleting (fresh etag) and re-confirm zero
   references (defends against a concurrent write between step 5 and
   step 6); ETag-gated CAS delete. If the doc was already deleted by a
   prior partial run, this is a no-op (idempotent).

## 6. Idempotency / resumability

Every step first checks current state before acting: skip creating
`sec_XNAS_PEP` if it exists; skip patching `config_PEP` if its
`security_id` is already `"XNAS:PEP"`; skip a `ledger_txn`/
`import_session` patch if its field already reads `"XNAS:PEP"`; skip
deleting `sec_NNYS_PEP` if it's already gone. A partially-failed run can
be safely re-invoked with `--apply` and will resume exactly where it left
off, re-verifying rather than re-doing completed steps. Report format
matches `migrate_legacy_symbol_config.py`'s `MigrationReport` dataclass
convention (per-step counts, `collision_ambiguous`/`currency_evidence:
inconclusive` flags, machine-readable JSON + human summary).

## 7. Modes, CLI, and exit codes (mirrors the approved migration-script
convention — no new pattern invented)

- `--audit` (default): read-only, runs §3 discovery + §2 evidence
  derivation, prints what *would* change (including the currency
  evidence verdict), writes nothing.
- `--backup-only`: performs §4 only.
- `--apply`: mandatory backup (§4) automatically, then §5 steps 1-6 in
  order, aborting at the first failed precondition.
- `--restore <backup_file>`: re-reads each backed-up doc's *current*
  state fresh (not blind overwrite) and CAS-replaces/recreates only if
  the live doc still reflects the post-migration state (defends against
  clobbering legitimate concurrent edits, e.g. a correction transaction
  entered after the repair); per-document failures are reported
  individually rather than aborting the whole restore.
- Exit codes: `0` normal (incl. idempotent no-op and `--audit`),
  `1` bad CLI args, `2` backup step failed / collision_ambiguous /
  discovery inconsistency (never proceeds to write), `3` post-apply
  verification failed (holdings mismatch or nonzero remaining
  references — `sec_NNYS_PEP` retained, not deleted).

## 8. Explicitly out of scope / untouched

- No financial fields (`quantity`, `gross`, `fees`, `net`, `fx`,
  `withholding`, `cost_basis_status`, `trade_date`) on any `ledger_txn`.
- No `movement_id` (`ledger_txn.id`) or `account_id` (partition key)
  changes — pure field patch, never create+delete for ledger docs.
- No `config_PEP.exchange` field change (legacy value preserved).
- No agent/alert/notification/warmup toggles on `config_PEP`.
- No `security_master` fields beyond `exchange_mic`, `security_id`,
  `listing_currency` (currency only if evidence-unanimous),
  `migrated_from`, `migration_note`, `updated_at`.
- `listing_currency` correction is conditional on unanimous ledger
  evidence — never applied on assumption alone.

## 9. Assignment

- **Livingston**: implement `backend/scripts/repair_pep_security_id.py`
  per §3-§7 exactly, reusing `make_security_id`/`security_id_to_doc_id`/
  `security_id_to_ticker` (`cosmos_securities.py`) and
  `LEGACY_ALIAS_TO_MIC` (`provider_symbols.py`) — no new mapping table.
  Script must accept `--from-security-id`/`--to-security-id` as
  overridable CLI args (defaulted to `NNYS:PEP`/derived-from-config, so
  the tool is parameterized rather than hardcoded, while today's only
  authorized/tested target is this one PEP case).
- **Basher**: `backend/tests/test_repair_pep_security_id.py` using fake
  Cosmos containers (matching `test_migrate_legacy_symbol_config.py`'s
  convention) — cover: happy path (create target, patch all references,
  delete source); target-already-exists case; collision_ambiguous
  abort (conflicting isin on candidate target); currency-evidence
  unanimous vs. inconclusive; holdings-equivalence check catching an
  injected mismatch (must abort before delete); idempotent re-run after
  simulated partial failure at each step; `--restore` re-reads live
  state rather than blind-overwriting.

## 10. Authorized files (exact)

- New: `backend/scripts/repair_pep_security_id.py`
- New: `backend/tests/test_repair_pep_security_id.py`
- No other file may be touched. No production Cosmos writes are
  authorized by this review — `--apply` must only be run against
  production after a separate, explicit authorization.
