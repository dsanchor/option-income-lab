# Saul — History

## Project Context
- Project: options-agent
- User: dsanchor
- Stack: Python, TypeScript, Microsoft Agent Framework, Azure Foundry
- Joined to independently close Dashboard Banner provider-contract freshness gaps.

## Learnings
- 2026-09-25: The real technical provider contract is emitted by
  `TechnicalsCalculator`: `technicals.summary` has exact
  `recommendation.{label,value}` plus integer `buy/sell/neutral` counts totaling
  25; `technicals.moving_averages` uses the same shape with counts totaling 15
  and exact `indicators.<allowlisted-name>.{label,value,formatted,signal}` entries.
- 2026-09-25: Dashboard Banner recommendation eligibility now reads only those
  exact paths and types, verifies score/count/label consistency, requires a real
  recent history timestamp plus recognized measured evidence, and ignores
  aliases (`name`, `score`, `summary`, case-normalized counts, alternate nesting).
  Exact SMA50 plus an alias envelope produces zero facts, zero counts/watermarks,
  no LLM call, and deterministic no-data persistence.
- 2026-09-25: Focused validation: 191 backend passes with the same 3 unrelated
  option-chain fixture failures; 6 frontend contracts pass; strict-contract
  probe, Python compilation, scoped Ruff, and changed-file diff checks pass.
- 2026-09-26: Simulate-a-Roll strike identity now uses one canonical Decimal
  contract from raw JSON through same-contract rejection, exact chain lookup,
  response serialization, and frontend payloads. Accepted strikes are positive
  plain decimals with 1-9 integer digits and up to 20 fractional digits;
  exponent notation, signs, booleans, nonfinite values, overflow, and excess
  precision fail closed. Redundant leading/trailing zeros normalize by numeric
  equality, while distinct 20-place chain strikes remain distinct.
- 2026-09-26: Validation passed 144 backend roll regressions, 5 focused
  frontend roll contracts, 5 independent high-precision probes, TypeScript,
  scoped ESLint, Python compilation, production build, and diff hygiene. The
  full frontend suite passed 1340/1342; its two failures are unrelated
  pre-existing paper-position/economics and sparse-ledger MovementDetail
  source-contract assertions. The build retained one pre-existing generated
  CSS warning; focused Ruff reports only legacy findings outside the new test
  and import-order change.
- 2026-09-26: Fixed the merged roll-simulator regression at the cache/view
  boundary: `OptionsChainCache.get_or_load_async()` returns serialized JSON,
  but the endpoint passed that string to `apply_agent_view()`, which correctly
  left non-dicts unchanged; simulation then rejected every request as an
  unavailable chain. The roll boundary now decodes the real cache payload (and
  the provider `options_chain` envelope), normalizes only canonical numeric
  bid/ask strings before the approved view, preserves Decimal strike identity,
  and matches both compact and hyphenated expiry keys exactly.
- 2026-09-26: Roll failures now distinguish retrieval (`chain_unavailable`),
  exact current/target misses, and current/target midpoint failures with the
  quote reason. Stale/carried values remain eligible with visible provenance;
  one-sided, zero, crossed, last/mark-only markets remain ineligible without
  fallback. Validation passed 271 backend chain/roll tests, 5 frontend
  contracts, TypeScript, scoped ESLint, Python compile/critical Ruff, production
  build, and diff hygiene; the build retained one pre-existing generated-CSS
  warning and broad Ruff retained legacy findings.
