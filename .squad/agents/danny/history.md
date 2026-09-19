# Danny — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Role:** design review, product contract, and final-gate owner
- **Stack:** Python, React, portfolio/economics architecture, documentation-first review workflow

## Core Context

- Danny captures user directives as durable contracts before implementation whenever ambiguity could create churn.
- Favors explicit route structures, crisp field semantics, and event-grain accounting over convenient but misleading UI shortcuts.
- Repeated project themes: portfolio ledger design, economics information architecture, symbol/screener rules, and reviewer gate criteria.
- History before 2026-09 was condensed on 2026-09-09 into this core summary for maintainability.

## Recent Learnings

### 2026-09-09 — Economics Unified Dashboards Design
- Established the three-view Economics information architecture: `/economics`, `/economics/options`, `/economics/dividends`.
- Rejected a blended total KPI in v1 because options economics is not yet FX-normalized while dividends are authoritative in EUR.

### 2026-09-09 — Fiscal Reports design
- Designed a future `/portfolio/fiscal-reports` feature for the Investments menu.
- Chose dividend reporting at composite `ca_group_id` event grain when present, with withholding taxonomy that distinguishes source-only, destination-only, both, and neither while treating absent and explicit-zero withholding as the same visible “no withholding” outcome.
- Deferred CSV export to a later backend-generated phase.

### 2026-09-08 — Share-consolidation and portfolio contract review
- Reinforced the pattern that reviewer feedback should resolve ambiguity at the model/contract layer before more UI or backend code is added.

### 2026-09-19 — Assigned option stock-link picker contract
- Found a read/write contract mismatch: stock `BUY`/`SELL` corrections support
  `ASSIGNMENT_STOCK`, but the shared linkable-position endpoint and frontend picker
  accept only option transaction types.
- Decided to extend the existing picker endpoint: `BUY` lists assigned puts missing a
  linked stock buy; `SELL` lists assigned calls missing a linked stock sell.
- Assignment eligibility must reuse the linkage service's active movement index so the
  picker and `OPTION_ASSIGNMENT_STOCK_MISSING` warning cannot drift.
- Stock dropdown selection should synchronize position ID, `ASSIGNMENT_STOCK`, and the
  candidate option type; no auto-matching or auto-creation.
