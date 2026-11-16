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
  test(`${label} shows next and last, independently of the other event's last-only state`, () => {
    const html = markup({
      [kind]: { next_date: "2026-11-20", last_date: "2026-08-01" },
      [otherKind]: { next_date: null, last_date: "2026-11-14" },
    });
    const event = eventMarkup(html, label);
    assert.match(event, /Next: <time dateTime="2026-11-20"/);
    assert.match(event, /Last: <time dateTime="2026-08-01"/);
    assert.doesNotMatch(event, /No next/);
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
    assert.match(eventMarkup(html, label), /No next date known at the moment/);
    assert.doesNotMatch(eventMarkup(html, label), /Date unavailable/);
    assert.doesNotMatch(eventMarkup(html, label), /Next:|Last:|<time/);
    assert.match(eventMarkup(html, otherLabel), /Next:/);
    assert.doesNotMatch(eventMarkup(html, otherLabel), /Last:|unavailable|No next/);
  });
}

test("absent calendar evidence never fabricates a date", () => {
  for (const calendar of [undefined, null, {}]) {
    const html = markup(calendar);
    assert.match(html, /<h3[^>]*>Calendar</);
    assert.equal((html.match(/No next date known at the moment/g) ?? []).length, 2);
    assert.doesNotMatch(html, /Date unavailable/);
    assert.doesNotMatch(html, /Next:|Last:|<time/);
  }
});

for (const [now, next, expected] of [
  ["2026-11-16T23:59:59Z", "2026-11-20", "in 4 days"],
  ["2026-11-16T23:59:59Z", "2026-11-17", "in 1 day"],
  ["2026-11-16T00:00:01Z", "2026-11-16", "today"],
  ["2026-12-31T12:00:00Z", "2027-01-01", "in 1 day"],
  ["2028-02-28T12:00:00Z", "2028-03-01", "in 2 days"],
  ["2026-03-07T23:59:59-05:00", "2026-03-09", "in 1 day"],
  ["2026-11-17T00:00:01Z", "2026-11-16", "1 day ago"],
]) {
  test(`next-date countdown is ${expected} at ${now}`, (t) => {
    t.mock.timers.enable({ apis: ["Date"], now: new Date(now) });
    const html = markup({
      earnings: { next_date: next, last_date: "2026-01-01" },
      ex_dividend: { next_date: next, last_date: null },
    });
    for (const label of ["Earnings", "Ex-dividend"]) {
      const event = eventMarkup(html, label);
      assert.ok(event.includes(`(${expected})`));
      assert.ok(event.includes(`dateTime="${next}"`));
      assert.equal((event.match(/\(.*?\)/g) ?? []).length, 1);
    }
  });
}

test("Calendar follows Projection Fan inside Summary, outside the US-options gate", () => {
  const page = readFileSync(new URL("../src/app/symbols/[symbol]/page.tsx", import.meta.url), "utf8");
  assert.match(page, /<SymbolForecastSummary[^>]+\/>\s*<\/Suspense>\s*\)}\s*<SymbolCalendar calendar=\{d\.calendar\} \/>\s*<\/DetailSection>/);
});
