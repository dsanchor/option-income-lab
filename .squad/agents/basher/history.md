# Basher — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Role:** test, regression, and reviewer-gate owner
- **Stack:** pytest, Node test runner, TypeScript contract checks, backend/frontend seam validation

## Core Context

- Basher validates production-shaped behavior, boundary conditions, persistence seams, and frontend/backend contract parity.
- Preferred test style: authoritative focused suites first, then broader confidence runs sized to the exact blast radius.
- Frontend verification may stay lightweight and source-contract based when the repo already favors typed/export contract tests over brittle component rendering.
- Durable rule: options economics uses the 100-share multiplier only for dollar-value calculations, never for counts or ratios.
- History before 2026-09 was condensed on 2026-09-09 into this core summary to keep the file usable.

## Recent Learnings

### 2026-09-09 — Dividends economics validation scope
- Added dedicated backend unit tests for dividends economics plus endpoint smoke coverage.
- Kept frontend validation intentionally lightweight with a contract test around `frontend/src/types/economics.ts` and the consuming views, matching existing repo testing patterns.

### 2026-09-08 — FIFO / net-accounting integration gate
- Cleared the FIFO integration gate after confirming stale failures were test-expectation drift rather than product defects.
- Reinforced the rule that reviewer verdicts must distinguish true implementation bugs from collateral stale tests.

### 2026-09-07 — Release-gate discipline
- Maintained the expectation that final release gates target zero unexpected skips/xfails inside the scoped authored suites and explicitly document any pre-existing unrelated failures.
