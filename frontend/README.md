# Option Income Lab — `web` (Next.js frontend)

The public entrypoint of [Option Income Lab](../README.md). A **Next.js 16 App Router** app
(React 19, TypeScript, Tailwind CSS v4, recharts) that runs as a **Backend-for-Frontend (BFF)**:
its server-side route handlers proxy browser requests to the internal FastAPI `api`
(`../backend/`). The browser never calls the API directly.

## Tech stack

Everything here lives in the **`web`** tier (the public container). The only piece that talks to
the `api` is the **BFF layer** — the route handlers under `src/app/api/**`, which run server-side
and read `API_BASE_URL`. Client components never fetch the API directly; they call same-origin
`/api/*` routes that proxy through.

| Library | Version | Role in the app | Where it lives |
|---|---|---|---|
| **Next.js** (App Router, Turbopack) | 16 | Framework. Server Components render pages; **route handlers under `app/api/**` are the BFF proxy** to the internal `api`. Built with `output: "standalone"` for a slim Node server. | `src/app/**` |
| **React** | 19 | UI runtime — mix of Server and Client Components (`"use client"`). | `src/app`, `src/components` |
| **TypeScript** | 5 | Static types; shared domain types are the source of truth for API shapes. | `src/types/**` |
| **Tailwind CSS v4** | 4 | Utility-first styling driven by **design tokens** (CSS custom properties) defined in `globals.css` (`--bg-card`, `--accent-blue`, `--radius`, gradients…). | `src/app/globals.css` + class names |
| **@tanstack/react-query** (+ devtools) | 5 | Client-side data fetching, caching, and auto-refresh for **interactive** views (dashboard auto-refresh, chat, live tables). Wired via a `Providers` wrapper. | `src/lib/query-client.tsx`, client components |
| **recharts** | 3 | All charts — economics (net income, calls/puts), forecast projection fan & hit-rate, position detail, symbol tech-timing history. | `src/components/*Charts*.tsx`, `ForecastHistory`, `PositionDetail` |
| **motion** (Framer Motion) | 12 | Micro-interactions & transitions — `StatCard` entrance (fade + slide-up), forecast detail **modal** enter/exit via `AnimatePresence`. | `StatCard.tsx`, `ForecastHistory.tsx` |
| **lucide-react** | 1 | Consistent, themeable **SVG icon set** — replaces ad-hoc emoji in the top navigation (logo, links, dropdowns, mobile menu). | `TopNav.tsx` |
| **sonner** | 2 | **Toast notifications** — a single dark-themed `<Toaster>` in the root layout; components call `toast()` for feedback (e.g. failed data loads). | `app/layout.tsx`, client components |
| **countup.js** | 2 | Animated number roll-ups behind the `AnimatedNumber` component used by KPI cards. | `AnimatedNumber.tsx`, `StatCard.tsx` |

> **Rule of thumb:** anything under `src/app/api/**` is **server-only** (it may read secrets /
> `API_BASE_URL` and reach the internal `api`). Everything in `src/components/**` marked
> `"use client"` runs in the browser and must go through those `/api/*` routes for data.

## Requirements

- Node.js 24+
- A running `api` (see [`../backend`](../backend)) reachable via `API_BASE_URL`

## Environment

| Variable | Required | Description |
|---|---|---|
| `API_BASE_URL` | Always | Base URL of the internal `api`. Read **at runtime** by server components / route handlers. Defaults to `http://localhost:8000` for local dev. |

## Local development

```bash
npm install
export API_BASE_URL="http://localhost:8000"   # point at your local api
npm run dev                                    # dev server on http://localhost:3000
```

Open **http://localhost:3000**.

## Build & run (production)

```bash
npm run build      # Turbopack build → .next/standalone (output: "standalone")
node .next/standalone/server.js   # serves on PORT (default 3000)
```

> **Note (OneDrive/WSL):** delete `.next` before building if you hit filesystem `EIO`
> errors: `rm -rf .next && npm run build`.

## Docker

```bash
docker build -t oil-web .
docker run -p 3000:3000 -e API_BASE_URL="http://host.docker.internal:8000" oil-web
```

The multi-stage `Dockerfile` produces a slim standalone runtime (Node 24, port 3000,
`node server.js`).

## Lint

```bash
npm run lint                 # whole project
npx eslint src/path/File.tsx # a single file (stricter React Compiler rules apply)
```

## Structure

```
src/
  app/          App Router pages (Server Components) + BFF route handlers under app/api/**
                (server-only proxy to the internal `api`). layout.tsx mounts TopNav + Toaster.
  components/   React components — charts (recharts), tables, chat, StatCard, TopNav,
                ForecastHistory/ForecastCharts, position detail… (client where interactive)
  lib/          Shared helpers — API client (apiFetch), React Query provider, formatting, badges
  types/        Shared TypeScript types (API response shapes — the contract with the `api`)
```

**Data flow:** browser → same-origin `/api/*` route handler (`src/app/api/**`, server) →
`apiFetch()` → internal FastAPI `api` (`API_BASE_URL`) → CosmosDB. The browser never reaches the
`api` directly.

See the repo [README](../README.md) and [docs/architecture.md](../docs/architecture.md) for the
full two-container topology and deployment.
