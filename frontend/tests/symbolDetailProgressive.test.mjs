import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { symbolDetailQueryOptions } from "../src/lib/symbol-detail-query.ts";

const require = createRequire(new URL("../package.json", import.meta.url));
const { QueryClient, QueryObserver } = require("@tanstack/react-query");
const page = readFileSync(new URL("../src/app/symbols/[symbol]/page.tsx", import.meta.url), "utf8");
const panels = readFileSync(new URL("../src/components/SymbolDetailPanels.tsx", import.meta.url), "utf8");
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

test("navigation has a loading shell and the first server request excludes slow detail datasets", () => {
  const loading = readFileSync(new URL("../src/app/symbols/[symbol]/loading.tsx", import.meta.url), "utf8");
  assert.match(loading, /Loading symbol detail/);
  assert.match(loading, /Summary/);
  assert.match(page, /\/detail\?section=summary/);
  assert.doesNotMatch(page, /await.*(positions|activities|plans|stocks)/i);
  assert.match(page, /<Suspense fallback=[\s\S]*<SymbolForecastSummary/);
  assert.match(page, /usOptionsEligible && \([\s\S]*<Suspense/);
  assert.match(panels, /symbolDetailQueryOptions\(symbol, "options", snapshot\)/);
  assert.match(panels, /symbolDetailQueryOptions\(symbol, "stocks", snapshot\)/);
  assert.match(panels, /symbolDetailQueryOptions\(symbol, "plans", snapshot\)/);
  assert.match(panels, /<StockTransactionsTable securityId=\{securityId\}/);
  assert.match(panels, /Could not load this section/);
  assert.match(panels, /Retry/);
  const tabs = readFileSync(new URL("../src/components/SymbolDetailTabs.tsx", import.meta.url), "utf8");
  assert.match(tabs, /ready \? panels\[tab\.id\]/);
  assert.match(tabs, /HASH_TO_TAB\[window\.location\.hash\]/);
  assert.match(tabs, /setReady\(true\)/);
});

test("summary subscriptions never load holdings; activated panels fetch independently and reuse cached data", async (t) => {
  const requests = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    requests.push(url);
    return { ok: true, json: async () => ({ portfolio: { current_shares: "0" }, positions: [], plans: [] }) };
  });
  const client = new QueryClient();
  const summary = new QueryObserver(client, { ...symbolDetailQueryOptions("XNAS:MSFT", "stocks", "initial"), enabled: false });
  const unwatchSummary = summary.subscribe(() => {});
  await flush();
  assert.equal(requests.length, 0);
  const panel = new QueryObserver(client, symbolDetailQueryOptions("XNAS:MSFT", "stocks", "initial"));
  const unwatchPanel = panel.subscribe(() => {});
  await flush();
  assert.deepEqual(requests, ["/api/symbols/XNAS%3AMSFT/detail?section=stocks"]);
  assert.equal(summary.getCurrentResult().data.portfolio.current_shares, "0");
  panel.setOptions(symbolDetailQueryOptions("XNAS:MSFT", "options", "initial"));
  await flush();
  assert.equal(requests.length, 2);
  assert.equal(requests.some((url) => url.includes("plans")), false);
  panel.setOptions(symbolDetailQueryOptions("XNAS:MSFT", "stocks", "initial"));
  await flush();
  assert.equal(requests.length, 2);
  await panel.refetch();
  assert.equal(requests.length, 3);
  panel.setOptions(symbolDetailQueryOptions("XNAS:MSFT", "plans", "initial"));
  await flush();
  assert.equal(requests.length, 4);
  unwatchPanel();
  unwatchSummary();
  client.clear();
});

test("refresh/navigation snapshots abort obsolete requests, retain canonical identity and isolate errors", async (t) => {
  let resolveOld;
  let oldSignal;
  let requests = 0;
  t.mock.method(globalThis, "fetch", (url, { signal }) => {
    requests++;
    if (requests === 1) {
      oldSignal = signal;
      return new Promise((resolve) => { resolveOld = resolve; });
    }
    return Promise.resolve({ ok: requests !== 2, json: async () => requests === 2
      ? { error: "section unavailable" } : { positions: [{ position_id: "new" }] } });
  });
  const client = new QueryClient();
  const panel = new QueryObserver(client, symbolDetailQueryOptions("XNAS:MSFT", "options", "old"));
  const unwatch = panel.subscribe(() => {});
  panel.setOptions(symbolDetailQueryOptions("XLON:MSFT", "options", "new"));
  assert.equal(oldSignal.aborted, true);
  await flush();
  assert.match(panel.getCurrentResult().error.message, /section unavailable/);
  await panel.refetch();
  resolveOld({ ok: true, json: async () => ({ positions: [{ position_id: "old" }] }) });
  await flush();
  assert.equal(panel.getCurrentResult().data.positions[0].position_id, "new");
  assert.equal(client.getQueryData(["symbol-detail", "XNAS:MSFT", "options", "old"]), undefined);
  unwatch();
  client.clear();
});
