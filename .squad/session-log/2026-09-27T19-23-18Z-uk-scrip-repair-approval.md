# Session Log: UK scrip normalization and legacy cost repair

**Timestamp:** 2026-09-27T19:23:18Z
**Status:** IMPLEMENTED, APPROVED, AND CONSOLIDATED

- Rusty finalized the Economics summary as Total Dividends plus two secondary
  cards, removing the duplicate standalone Scrip Dividends card.
- Linus fixed UK Partial coverage by normalizing Yahoo `GBp`/`GBX` quotes to
  GBP in the shared quote boundary used by backfill and Yahoo-on-save.
- Danny accepted a separate persisted-evidence-only legacy cost repair.
- Reuben implemented bounded diagnostics and a dry-run-first, SHA-confirmed,
  backup/CAS/restore migration that fails closed on contradictory evidence and
  duplicate ACTIVE share-leg ambiguity.
- Basher approved the final worktree after 370 targeted backend tests and
  frontend, type, lint, and diff checks passed.
- Scribe merged and deduplicated the inbox decision, updated histories and
  current identity, cleared the inbox, and committed only `.squad` changes.
