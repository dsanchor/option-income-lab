# Rusty — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Role:** frontend, BFF, and integration owner
- **Stack:** Python, FastAPI/BFF, React, App Router, TypeScript

## Core Context

- Owns routed UX, BFF proxy wiring, and frontend contract alignment with backend decisions.
- Prefers shared layouts, typed transport contracts, and query-parameter carry-over instead of duplicated page scaffolding.
- Normalize and guard at the boundary: nullable or sparse backend data should render safely without inventing values.
- Keep user-facing labels and navigation semantics consistent across symbols, portfolio, and economics surfaces.
- History before 2026-09 was condensed on 2026-09-09 into this core summary to keep the file under control.

## Recent Learnings

### 2026-09-09 — Economics frontend tabs, overview, and dividends detail
- Implemented the canonical routed Economics structure: `/economics`, `/economics/options`, and `/economics/dividends`.
- Used a shared `layout.tsx` tab strip and a second dividends fetch for all-history comparison sections so YoY and snowball views stay truthful while the main page still respects scoped filters.
- Updated BFF routes and `frontend/src/types/economics.ts` to carry the new dividends report fields.

### 2026-09-08 — Sparse manual movement defensive guards
- Hardened detail/table components and widened types so legacy/manual movements that omit financial blocks do not crash the UI.

### 2026-09-06 — Portfolio routed frontend conventions
- Reinforced the pattern of thin BFF proxies, shared stat-card presentation, and account-name-only informational labels across portfolio surfaces.
