# Internal Finance News MCP

Isolated deployment of [FinanceNews-MCP](https://github.com/guangxiangdebizi/FinanceNews-MCP),
as listed on [MCP Market](https://mcpmarket.com/server/finance-news).
The Dockerfile builds unmodified upstream commit
`6243c388da63d94562cf8286b00fbed03076d217` (package 1.1.0), verifies its archive SHA-256,
and installs its locked dependencies with `npm ci`. Upstream source and Apache-2.0
license are retained in the image. No financial-news logic is reimplemented.

## Deployment

`.github/workflows/deploy-financial-news-mcp.yml` runs on changes to this directory
or its workflow on `main`, or manually via **Actions → Deploy Finance News MCP**.
Other branches cannot publish/deploy with this workflow. It independently tests,
builds, smoke-tests and publishes `ghcr.io/<owner>/option-income-lab-financial-news-mcp`,
then deploys the exact digest. It does not deploy the API or frontend.

The `production` GitHub environment must contain:

| Secret | Purpose |
| --- | --- |
| `FINNHUB_API_KEY` | Existing Finnhub credential; runtime only |
| `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | Existing Azure OIDC login |

Optional GitHub repository/environment variables:

| Variable | Default |
| --- | --- |
| `AZURE_RESOURCE_GROUP` | `stock-options-manager-rg` |
| `AZURE_CONTAINER_APPS_ENVIRONMENT` | `cae-stock-options-manager` |
| `AZURE_FINANCIAL_NEWS_APP` | `ca-stock-options-financial-news` |

The resource group and Container Apps Environment **must already exist**. The OIDC
identity needs read access to that environment and Container App read/write/revision
read permissions in the group (the existing resource-group Contributor role suffices).
No new environment, registry, managed identity, or public ingress is created.

### One-time GHCR visibility prerequisite

A newly published GHCR package may initially be **private**, even for a public
repository. After the first publish, open GitHub profile/organization **Packages →
option-income-lab-financial-news-mcp → Package settings → Change visibility → Public**,
then rerun the workflow. Public visibility exposes only the container/source, not
the MCP service or Finnhub key.

The deployment first verifies an **anonymous** pull of the exact digest. If it
fails, Azure is not changed. No expiring `GITHUB_TOKEN` is saved as a registry
credential; it is used only to publish. No additional registry secret or paid ACR
is required. If package visibility is organization-restricted, this prerequisite
must be resolved before deployment can succeed.

## Runtime and lifecycle

- HTTPS internal ingress (`external: false`), target port 3000, `/mcp` endpoint.
- Reachable only by clients **in the same existing Container Apps Environment**;
  not directly from desktop MCP clients or the public internet.
- Upstream JSON-RPC HTTP transport: initialize first, then send the returned
  `Mcp-Session-Id` header on subsequent requests. No additional access authentication
  is introduced; all clients in that environment are trusted. Upstream permits
  request-header API-key overrides; network isolation is the access boundary.
- `FINNHUB_API_KEY` is a Container App secret referenced by `secretRef`, not a
  Docker build argument. Deployment removes it from the subprocess environment
  and sends it to ARM in memory over HTTPS. No secret-bearing parameter files,
  command-line arguments, deployment outputs, or artifacts are produced.
- Non-root Node 24 process, 0.25 vCPU / 0.5 GiB, min/max one replica, single revision.
  `/health` drives readiness and liveness probes. A replica is kept warm because
  upstream sessions are in memory. Restarts and deployments lose sessions;
  callers must reinitialize. A new revision may briefly overlap the old one during
  rollout; this is not durable session storage.
- Every deployment gets a unique revision suffix, including reruns with the same
  digest, so secret rotation takes effect without a separate restart.
- The dedicated deployment concurrency group does not cancel a running deployment.
  GitHub concurrency may replace older pending runs with a newer pending run.
- Create/update uses the same ARM resource PUT and refuses to overwrite an unowned
  app or move an existing app to another environment. Only this app is modified.
  Deployment waits up to 60 polls (10-second intervals, excluding request time)
  for its exact revision to be healthy, active, and latest-ready; failures are
  reported without claiming success. No automatic rollback is performed.

The successful Actions summary prints the internal HTTPS `/mcp` URL.
This workflow does not change application MCP configuration.

## API Chat integration

API Chat alone can optionally use `get_financial_news` alongside the existing
read-only `oil-mcp` business tools. The agent chooses whether news is relevant;
there is no unconditional news fetch. Other chat modes and Option Timing are
unchanged. Queries contain public company/ticker/topic information only, never
conversation history or private portfolio/account data. News output is treated
as untrusted evidence, checked for relevance, and cited with returned source URLs
and publication dates when available; freshness and completeness are not guaranteed.
API Chat no longer sets its former 1,500-token output cap: provider defaults and
model hard limits apply. Request/history safety bounds and other agents' output
limits remain unchanged.

The API container needs only the server-configured `FINANCIAL_NEWS_MCP_URL`
(internal HTTPS `/mcp` URL), **not** a Finnhub key or extra headers. If unset, or
if optional discovery fails, API Chat keeps its business tools and explicitly
discloses missing news access when needed. Discovery is bounded to 15 seconds;
news MCP requests have a 10-second timeout. Invocation errors remain errors, not
successful news results.

The main `docker-publish.yml` production API deployment discovers
`AZURE_FINANCIAL_NEWS_APP` (default above) in `AZURE_RESOURCE_GROUP`. It sets the
URL only when the news app has internal ingress in the API's own environment;
an absent/incompatible app clears the optional URL without blocking deployment.
It never modifies the separate news app. Deploy news first, then deploy the API.

Fresh Bicep provisioning derives the same internal URL from the environment's
domain, without creating or depending on the separate news app. The optional
`apps.api.financialNewsAppName` provisioner config property overrides the default
`ca-stock-options-financial-news`; `""` disables news. Keep it aligned with the
Actions variable for subsequent main API deployments.

The existing Agent Framework 1.4.0 SDK handles transport, sessions, discovery,
and invocation. Upstream negotiates MCP `2024-11-05`, supports no prompts, and
rejects `ping` with HTTP 400 (which aborts the SDK transport). A news-only
subclass skips that optional SDK connectivity probe; prompts and session DELETE
are disabled. Real SDK smoke against the unmodified pinned server validated
session-bound discovery and local validation-error propagation without calling
Finnhub or an LLM. No raw JSON-RPC client or upstream fork is introduced.

## Validation

```bash
python3 -m unittest discover -s mcp/financial-news/tests -v
docker build -t financial-news-mcp:local mcp/financial-news
docker run --rm -p 127.0.0.1:3000:3000 \
  -e FINNHUB_API_KEY=smoke-test-not-a-live-key financial-news-mcp:local
# In another terminal:
python3 mcp/financial-news/smoke.py http://127.0.0.1:3000
```

CI runs the smoke test against the built image. It checks `/health`, MCP initialize,
session-bound `tools/list`, and rejection without a session. It deliberately does
not invoke Finnhub or claim validation of live market data. Deploy-time health
uses the platform probes, since the runner cannot reach an internal-only service.

To upgrade, intentionally change the upstream commit and archive SHA-256 in the
Dockerfile together, review upstream changes/license, and rerun the tests. Keep
Node on a supported, explicitly versioned release; update its manifest digest in
both build and runtime stages together.
