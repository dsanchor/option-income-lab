FROM node:24.14.1-bookworm-slim@sha256:b506e7321f176aae77317f99d67a24b272c1f09f1d10f1761f2773447d8da26c AS build
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN curl --fail --location --silent --show-error \
      https://codeload.github.com/guangxiangdebizi/FinanceNews-MCP/tar.gz/6243c388da63d94562cf8286b00fbed03076d217 \
      --output source.tar.gz \
    && echo "44b26d3bb342f5ad6594e0e8838c291c0be17dce44ee8034dc59cfae9ebf8ef8  source.tar.gz" | sha256sum --check \
    && tar -xzf source.tar.gz --strip-components=1 \
    && rm source.tar.gz \
    && npm ci --no-audit --no-fund \
    && npm run build \
    && npm prune --omit=dev --no-audit --no-fund

FROM node:24.14.1-bookworm-slim@sha256:b506e7321f176aae77317f99d67a24b272c1f09f1d10f1761f2773447d8da26c
LABEL org.opencontainers.image.title="FinanceNews-MCP" \
      org.opencontainers.image.source="https://github.com/dsanchor/option-income-lab" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.description="Unmodified guangxiangdebizi/FinanceNews-MCP at 6243c388da63d94562cf8286b00fbed03076d217"
ENV NODE_ENV=production PORT=3000
WORKDIR /app
# Retain upstream source, license, and any notices alongside the compiled server.
COPY --from=build --chown=node:node /app /app
USER node
EXPOSE 3000
CMD ["node", "build/index.js", "--http"]
