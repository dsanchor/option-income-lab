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
