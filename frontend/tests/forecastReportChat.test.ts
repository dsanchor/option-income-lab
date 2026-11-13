import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(new URL("../package.json", import.meta.url));
const ts = require("typescript");
const component = readFileSync(
  new URL("../src/components/ForecastReportChat.tsx", import.meta.url),
  "utf8",
);
const page = readFileSync(
  new URL("../src/app/symbols/[symbol]/forecasts/page.tsx", import.meta.url),
  "utf8",
);
const route = readFileSync(
  new URL("../src/app/api/symbols/[symbol]/forecasts/chat/route.ts", import.meta.url),
  "utf8",
);
const providers = readFileSync(
  new URL("../src/components/AiProvidersView.tsx", import.meta.url),
  "utf8",
);

test("forecast page places the keyed chat beside range controls", () => {
  assert.match(page, /<ForecastReportChat key=\{`\$\{symbol\}:\$\{range\}`\}/);
  assert.match(page, /Report &amp; Chat|ForecastReportChat/);
  assert.ok(
    page.indexOf("<ForecastReportChat") < page.indexOf("<StatCard"),
    "chat must render above top-line stats",
  );
});

test("chat requests only after expansion and sends documented payloads", () => {
  assert.match(component, /onClick=\{open\}/);
  assert.match(component, /mode: "initial", range: chatRange/);
  assert.match(component, /mode: "follow_up"/);
  assert.match(component, /history: messages/);
  assert.doesNotMatch(component, /maxLength|messages\.slice\(|question\.slice\(/);
  assert.doesNotMatch(component, /useEffect\(\(\) => \{\s*void request/);
});

function loadRoute(fetch: (url: string, options: RequestInit) => Promise<Response>) {
  const compiled = ts.transpileModule(route, {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText;
  const loaded = { exports: {} as { POST: (req: Request, context: {
    params: Promise<{ symbol: string }>;
  }) => Promise<Response> } };
  new Function("require", "exports", "fetch", compiled)((name: string) => {
    if (name === "@/lib/api") return { API_BASE_URL: "https://backend.test" };
    if (name === "@/lib/symbolEncoding") return { decodeSymbolParam: (value: string) => value };
    throw new Error(`Unexpected dependency ${name}`);
  }, loaded.exports, fetch);
  return loaded.exports.POST;
}

test("BFF forwards long valid conversations and full upstream replies without body ceilings", async () => {
  const history = Array.from({ length: 13 }, (_, index) => ({
    role: index % 2 === 0 ? "assistant" : "user",
    content: "é".repeat(10001),
  }));
  const body = JSON.stringify({
    mode: "follow_up", range: "30d", message: "question ".repeat(1000), history,
  });
  const reply = "Full model response ".repeat(2000);
  assert.ok(new TextEncoder().encode(body).byteLength > 128 * 1024);
  let calls = 0;
  const post = loadRoute(async (url, options) => {
    calls += 1;
    assert.equal(url, "https://backend.test/api/symbols/MSFT/forecasts/chat");
    assert.deepEqual(JSON.parse(options.body as string), JSON.parse(body));
    return Response.json({ reply });
  });
  for (const withContentLength of [true, false]) {
    const response = await post(new Request("https://frontend.test/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(withContentLength ? { "Content-Length": String(Buffer.byteLength(body)) } : {}),
      },
      body,
    }), { params: Promise.resolve({ symbol: "MSFT" }) });
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { reply });
  }
  assert.equal(calls, 2);
});

test("BFF still rejects invalid transport/schema and surfaces upstream failures", async () => {
  let calls = 0;
  const post = loadRoute(async () => {
    calls += 1;
    return Response.json({ error: "Provider context window exceeded" }, { status: 400 });
  });
  for (const [body, contentType, status] of [
    ["{}", "text/plain", 415],
    ["{", "application/json", 400],
    ['{"context":"untrusted facts"}', "application/json", 400],
  ] as const) {
    const response = await post(new Request("https://frontend.test/chat", {
      method: "POST", headers: { "Content-Type": contentType }, body,
    }), { params: Promise.resolve({ symbol: "MSFT" }) });
    assert.equal(response.status, status);
  }
  assert.equal(calls, 0);
  const response = await post(new Request("https://frontend.test/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: '{"mode":"initial","range":"30d"}',
  }), { params: Promise.resolve({ symbol: "MSFT" }) });
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: "Provider context window exceeded" });
});

test("chat exposes conversation, retry, scrolling and accessibility contracts", () => {
  for (const contract of [
    'aria-expanded={opened}',
    'aria-controls="forecast-report-chat-panel"',
    'role="region"',
    'aria-live="polite"',
    "Retry report",
    "failedQuestion && send(failedQuestion)",
    "max-h-[52vh]",
    "overflow-y-auto",
    "nearBottomRef",
    "renderMarkdown",
    "shiftKey",
  ]) {
    assert.ok(component.includes(contract), `missing ${contract}`);
  }
  assert.match(component, /requestRef\.current \+= 1/);
  assert.match(component, /abortRef\.current\?\.abort\(\)/);
});

test("BFF requires JSON media type, allowlists fields and preserves status", () => {
  assert.match(route, /rawBody\.length > 0 && mediaType !== "application\/json"/);
  assert.match(route, /status: 415/);
  assert.ok(
    route.indexOf('mediaType !== "application/json"') <
      route.indexOf("JSON.parse(rawBody)"),
    "unsupported media types must be rejected before parsing",
  );
  assert.ok(
    route.indexOf('mediaType !== "application/json"') <
      route.indexOf("await fetch("),
    "unsupported media types must not be rewritten and forwarded",
  );
  assert.match(route, /new Set\(\["mode", "range", "message", "history"\]\)/);
  assert.match(route, /Object\.entries\(incoming\)\.filter/);
  assert.match(route, /Unknown field\(s\)/);
  assert.match(route, /status: upstream\.status/);
  assert.match(route, /Content-Type": "application\/json"/);
});

test("AI Providers remains registry-driven without a forecast special case", () => {
  assert.match(providers, /initial\.functions/);
  assert.doesNotMatch(providers, /forecast_report_chat/);
});
