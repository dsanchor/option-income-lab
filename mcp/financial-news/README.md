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
then deploys the exact digest. Existing application deployment files are unchanged.

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
No application MCP configuration is automatically changed.

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
