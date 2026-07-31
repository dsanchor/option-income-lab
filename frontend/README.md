# Option Income Lab — `web` (Next.js frontend)

The public entrypoint of [Option Income Lab](../README.md). A **Next.js 16 App Router** app
(React 19, TypeScript, Tailwind CSS v4, recharts) that runs as a **Backend-for-Frontend (BFF)**:
its server-side route handlers proxy browser requests to the internal FastAPI `api`
(`../backend/`). The browser never calls the API directly.

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
  app/          App Router pages + BFF route handlers under app/api/**
  components/   Client/server React components (charts, tables, chat, position detail…)
  lib/          Shared helpers (API client, formatting, badges, markdown)
  types/        Shared TypeScript types
```

See the repo [README](../README.md) and [docs/architecture.md](../docs/architecture.md) for the
full two-container topology and deployment.
