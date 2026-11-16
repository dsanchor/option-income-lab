import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(new URL("../package.json", import.meta.url));
const ts = require("typescript");
const { renderToStaticMarkup } = require("react-dom/server");
const { outputText } = ts.transpileModule(
  readFileSync(new URL("../src/components/SymbolCalendar.tsx", import.meta.url), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } },
);
const loaded = { exports: {} };
new Function("require", "module", "exports", outputText)(require, loaded, loaded.exports);
const Calendar = loaded.exports.default;

function markup(calendar) {
  return renderToStaticMarkup(Calendar({ calendar }));
}

function eventMarkup(html, label) {
  const escaped = label.replaceAll("-", "\\-");
  return html.match(new RegExp(`<dt[^>]*>${escaped}</dt><dd>(.*?)</dd>`))[1];
}

for (const [kind, label, otherKind, otherLabel] of [
  ["earnings", "Earnings", "ex_dividend", "Ex-dividend"],
  ["ex_dividend", "Ex-dividend", "earnings", "Earnings"],
]) {
  test(`${label} prefers next, independently of the other event's last-only state`, () => {
    const html = markup({
      [kind]: { next_date: "2026-11-20", last_date: "2026-08-01" },
      [otherKind]: { next_date: null, last_date: "2026-11-14" },
    });
    const event = eventMarkup(html, label);
    assert.match(event, /Next: <time dateTime="2026-11-20"/);
    assert.doesNotMatch(event, /Last:|2026-08-01|No next/);
    const otherEvent = eventMarkup(html, otherLabel);
    assert.match(otherEvent, /Last: <time dateTime="2026-11-14"/);
    assert.match(otherEvent, /No next date known at the moment/);
    assert.doesNotMatch(otherEvent, /Next:/);
  });

  test(`${label} is honestly unavailable without either date while the other stays next`, () => {
    const html = markup({
      [kind]: { next_date: null, last_date: null },
      [otherKind]: { next_date: "2026-11-20", last_date: null },
    });
    assert.match(eventMarkup(html, label), /Date unavailable/);
    assert.doesNotMatch(eventMarkup(html, label), /Next:|Last:|<time/);
    assert.match(eventMarkup(html, otherLabel), /Next:/);
    assert.doesNotMatch(eventMarkup(html, otherLabel), /unavailable|No next/);
  });
}

test("absent calendar evidence never fabricates a date", () => {
  for (const calendar of [undefined, null, {}]) {
    const html = markup(calendar);
    assert.match(html, /<h3[^>]*>Calendar</);
    assert.equal((html.match(/Date unavailable/g) ?? []).length, 2);
    assert.doesNotMatch(html, /Next:|Last:|<time/);
  }
});

test("Calendar follows Projection Fan inside Summary, outside the US-options gate", () => {
  const page = readFileSync(new URL("../src/app/symbols/[symbol]/page.tsx", import.meta.url), "utf8");
  assert.match(page, /<SymbolForecastSummary[^>]+\/>\s*<\/Suspense>\s*\)}\s*<SymbolCalendar calendar=\{d\.calendar\} \/>\s*<\/DetailSection>/);
});
