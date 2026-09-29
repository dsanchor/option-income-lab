import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

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
  assert.doesNotMatch(component, /useEffect\(\(\) => \{\s*void request/);
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
