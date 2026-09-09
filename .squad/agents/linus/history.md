# Linus — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Role:** quantitative strategy, provider contract, prompt, and financial-rules owner
- **Stack:** Python, provider adapters, options-chain math, portfolio accounting rules, React support contracts

## Core Context

- Linus owns deterministic strategy logic, provider evidence normalization, and contract-safe financial calculations.
- Durable themes: earnings gates, DTE/roll policy, options-chain validity rules, screening universe semantics, and holdings/cost-basis math.
- Core implementation style: fail closed on missing evidence, keep JSON/output contracts explicit, and separate derived metrics from raw observed fields.
- History before 2026-09 was condensed on 2026-09-09 into this core summary to control file growth.

## Recent Learnings

- **2026-09-08:** Fixed the ECB FX parser bug where a `continue` skipped same-line `<Cube currency=...>` rates, emptying the non-EUR cache.
- **2026-09-07:** Re-established backend-authored `us_options_eligible` and `screener_eligible` booleans so the frontend no longer re-implements screener universe logic.
- **2026-09-06:** Locked portfolio summary cost basis to true CMP residual cost rather than `purchases - sales` arithmetic.
- **2026-09-03:** Continued the six-state Buy Tracker direction: deterministic evidence, explicit hard gates, and signed-score semantics.
