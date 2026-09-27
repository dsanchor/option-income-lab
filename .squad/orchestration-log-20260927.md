# Orchestration Log — 2026-09-27

## Dividend · Buy independent share FMV and backfill — 07:38:08Z

- Copilot directed separation of personal contribution from Dividend · Buy
  share FMV, with a safe Yahoo opening-price backfill and no Economics changes.
- Danny completed Design Review and accepted the independent `share_fmv`
  contract, including FIFO isolation, provenance, correction, backup, and
  migration safety requirements.
- Livingston implemented backend schema, persistence, API, corrections, and
  backup. Basher later rejected the integrated backend for unsupported currency
  acceptance, native/EUR net conflation, and explicit-null backup rejection.
- Rusty implemented the contribution/FMV-separated form, request shaping,
  correction behavior, and detail display without Economics aggregation.
- Linus implemented directed unadjusted Yahoo daily Open retrieval, historical
  ECB effective-date FX, and the deterministic dry-run/apply/backup/CAS/restore
  backfill workflow.
- Reuben, as independent revision owner under rejection lockout, fixed exact
  supported-currency validation, native `net.amount`, and explicit-null
  `share_fmv` backup handling.
- Basher approved the final integration after all 64 FMV-specific backend tests,
  7 new frontend tests, former blocker probes, TypeScript, and diff hygiene
  passed. Two backend option-chain assertions and one frontend Economics PP-6
  assertion remained documented unrelated baseline failures.
- Scribe merged and deduplicated six inbox records, preserved the full
  rejection/revision chronology, updated contributor histories and project
  identity, and committed only `.squad` records. Economics remains future
  scope.

## Scrip Economics and Yahoo FMV-on-save finalization — 18:21:33Z

- Danny accepted event-level cash+scrip Economics and the amendment allowing a
  server-only Yahoo Open valuation instruction during Dividend · Buy save.
- Linus implemented movement deduplication/event grouping, scrip economic
  value and coverage propagation, the shared Yahoo/ECB FMV service, atomic
  create/correction, idempotency, and backfill reuse.
- Rusty implemented combined cards, tables, charts, cumulative/YoY and
  overview presentation, Partial coverage, Dividend Count card removal, and
  Yahoo selection/loading/retry/correction UX.
- Basher approved the exact final worktree after 255 backend and 129 frontend
  tests, TypeScript, five focused visual-parity checks, and diff hygiene passed.
- Scribe consolidated four inbox records into two canonical decisions,
  superseded the prior unimplemented Economics boundary, updated project and
  agent histories, cleared the inbox, and committed only `.squad` changes.

## UK scrip normalization and legacy cost repair — 19:23:18Z

- Rusty removed the duplicate standalone Scrip Dividends card, leaving Total
  Dividends plus two secondary summary cards.
- Linus traced UK Partial coverage to pence-denominated Yahoo quotes and
  normalized `GBp`/`GBX` to GBP at the shared authoritative quote boundary used
  by both backfill and Yahoo-on-save.
- Danny accepted a separate fail-closed legacy cost-repair migration rather
  than weakening Economics eligibility or extending the FMV backfill.
- Reuben implemented bounded diagnostics and
  `repair_legacy_scrip_costs`, including dry-run planning, backup-before-write,
  CAS, restore, contradictory-amount rejection, and duplicate ACTIVE-leg
  ambiguity safeguards.
- Basher issued final **APPROVE** after 370 targeted backend tests and the
  frontend, type, lint, and diff gates passed.
- Scribe merged the inbox decision, updated project and contributor histories,
  and committed only `.squad` records.
