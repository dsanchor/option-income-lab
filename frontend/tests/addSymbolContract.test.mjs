/**
 * addSymbolContract.test.mjs — Single Add Symbol frontend source-contract tests.
 *
 * Ref: .squad/decisions/inbox/danny-single-add-symbol-contract.md
 *
 * These are source-contract tests: we read the actual TypeScript/TSX source
 * and assert that the contract's structural invariants hold. No React rendering.
 *
 * Coverage:
 *   AF-1  portfolio-api.ts exports `addSymbol`
 *   AF-2  addSymbol calls POST /api/symbols/add (canonical endpoint)
 *   AF-3  AddSymbolResponse type includes warmup_started field
 *   AF-4  BFF POST handler absent from /api/symbols/route.ts (Rusty's removal)
 *   AF-5  BFF GET handler present in /api/symbols/route.ts (must remain)
 *   AF-6  DgiScreenerView.tsx imports addSymbol from portfolio-api
 *   AF-7  DgiScreenerView.tsx AddButton calls addSymbol() — no raw POST /api/symbols
 *   AF-8  DgiScreenerView.tsx does flag toggle via PUT after addSymbol (step 2)
 *   AF-9  DgiScreenerView.tsx does not send flags in the addSymbol create body
 *   AF-10 addSymbol's create body shape matches AddSymbolCreateBody (canonical contract)
 *   AF-11 toExchangeMic helper present — maps DGI exchange strings to MIC
 *   AF-12 toExchangeMic: NASDAQ* → XNAS
 *   AF-13 toExchangeMic: NYSE fallback → XNYS
 *   AF-14 AddSymbolBody type covers both select (security_id) and create shapes
 *   AF-15 addSymbol is used (not raw fetch) — no direct POST to /api/symbols
 *
 * Run: node --test frontend/tests/addSymbolContract.test.mjs
 */

import { readFileSync } from "fs";
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");

// ── Source files ─────────────────────────────────────────────────────────────

const portfolioApiSrc = readFileSync(join(root, "src/lib/portfolio-api.ts"), "utf8");
const routesSrc = readFileSync(join(root, "src/app/api/symbols/route.ts"), "utf8");
const screenerSrc = readFileSync(join(root, "src/components/DgiScreenerView.tsx"), "utf8");

// ── AF-1..3: portfolio-api.ts contract ───────────────────────────────────────

describe("AF-1..3: portfolio-api.ts addSymbol canonical function", () => {
  it("AF-1: exports addSymbol function", () => {
    assert.ok(
      portfolioApiSrc.includes("export async function addSymbol") ||
      portfolioApiSrc.includes("export function addSymbol"),
      "AF-1: addSymbol must be exported from portfolio-api.ts"
    );
  });

  it("AF-2: addSymbol calls POST /api/symbols/add (canonical endpoint)", () => {
    // Must contain the /api/symbols/add URL
    assert.ok(
      portfolioApiSrc.includes("/api/symbols/add"),
      "AF-2: addSymbol must call /api/symbols/add, not /api/symbols"
    );

    // Must NOT call the legacy /api/symbols (bare, without /add)
    // Extract the addSymbol function body and verify
    const addSymbolIdx = portfolioApiSrc.indexOf("export async function addSymbol");
    assert.ok(addSymbolIdx !== -1, "addSymbol function not found");

    // The function body should reference /api/symbols/add
    const fnBody = portfolioApiSrc.slice(addSymbolIdx, addSymbolIdx + 500);
    assert.ok(
      fnBody.includes("/api/symbols/add"),
      `AF-2: addSymbol body must reference /api/symbols/add. Found: ${fnBody.slice(0, 200)}`
    );
    assert.ok(
      !fnBody.includes('"/api/symbols"') && !fnBody.includes("'/api/symbols'"),
      `AF-2: addSymbol must not use bare /api/symbols endpoint`
    );
  });

  it("AF-3: AddSymbolResponse type includes warmup_started field", () => {
    // The interface must declare warmup_started (optional is fine)
    const interfaceIdx = portfolioApiSrc.indexOf("interface AddSymbolResponse");
    assert.ok(interfaceIdx !== -1, "AF-3: AddSymbolResponse interface not found");
    // Find the closing brace of the interface
    const interfaceEnd = portfolioApiSrc.indexOf("}", interfaceIdx);
    const interfaceBody = portfolioApiSrc.slice(interfaceIdx, interfaceEnd + 1);
    assert.ok(
      interfaceBody.includes("warmup_started"),
      `AF-3: AddSymbolResponse must include warmup_started field. Interface: ${interfaceBody}`
    );
  });

  it("AF-14: AddSymbolBody covers both select and create shapes", () => {
    assert.ok(
      portfolioApiSrc.includes("AddSymbolSelectBody") &&
      portfolioApiSrc.includes("AddSymbolCreateBody") &&
      portfolioApiSrc.includes("AddSymbolBody"),
      "AF-14: AddSymbolBody must be a union of AddSymbolSelectBody | AddSymbolCreateBody"
    );
    assert.ok(
      portfolioApiSrc.includes("security_id: string"),
      "AF-14: AddSymbolSelectBody must have security_id: string"
    );
    assert.ok(
      portfolioApiSrc.includes("exchange_mic: string"),
      "AF-14: AddSymbolCreateBody must have exchange_mic: string"
    );
  });
});

// ── AF-4..5: BFF route.ts contract ───────────────────────────────────────────

describe("AF-4..5: BFF /api/symbols/route.ts", () => {
  it("AF-4: POST handler is absent from route.ts (legacy BFF POST removed)", () => {
    // Must NOT have an exported POST function
    const hasPostExport =
      routesSrc.includes("export async function POST") ||
      routesSrc.includes("export function POST") ||
      routesSrc.match(/export\s+\{[^}]*\bPOST\b[^}]*\}/);
    assert.ok(
      !hasPostExport,
      "AF-4: BFF POST /api/symbols handler must be removed. " +
      "Rusty: delete the POST export from route.ts. " +
      "Callers must use POST /api/symbols/add via addSymbol()."
    );
  });

  it("AF-5: GET handler is present in route.ts (list endpoint must remain)", () => {
    assert.ok(
      routesSrc.includes("export async function GET") ||
      routesSrc.includes("export function GET"),
      "AF-5: GET /api/symbols handler must remain in route.ts"
    );
  });

  it("AF-5: GET handler proxies to backend /api/symbols", () => {
    const getIdx = routesSrc.indexOf("GET");
    assert.ok(getIdx !== -1);
    // The GET function body should reference /api/symbols
    const nearGet = routesSrc.slice(getIdx, getIdx + 300);
    assert.ok(
      nearGet.includes("/api/symbols"),
      "AF-5: GET handler must proxy to backend /api/symbols"
    );
  });
});

// ── AF-6..10: DgiScreenerView.tsx AddButton contract ─────────────────────────

describe("AF-6..10: DgiScreenerView.tsx AddButton canonical flow", () => {
  it("AF-6: DgiScreenerView imports addSymbol from portfolio-api", () => {
    assert.ok(
      screenerSrc.includes("addSymbol") && screenerSrc.includes("portfolio-api"),
      "AF-6: DgiScreenerView must import addSymbol from @/lib/portfolio-api"
    );
    // Check the import line specifically
    const importLine = screenerSrc
      .split("\n")
      .find(l => l.includes("addSymbol") && l.includes("import"));
    assert.ok(importLine, "AF-6: no import line found for addSymbol");
    assert.ok(
      importLine && importLine.includes("portfolio-api"),
      `AF-6: addSymbol import must come from portfolio-api. Found: ${importLine}`
    );
  });

  it("AF-7: AddButton calls addSymbol() — not raw fetch to /api/symbols", () => {
    // Must use addSymbol(...)
    assert.ok(
      screenerSrc.includes("await addSymbol("),
      "AF-7: AddButton must call addSymbol() from portfolio-api"
    );

    // Must NOT contain a raw POST to /api/symbols (legacy pattern)
    // Look for fetch('/api/symbols' or fetch("/api/symbols" without /add
    const rawPostPattern = /fetch\s*\(\s*["'`]\/api\/symbols["'`]/;
    assert.ok(
      !rawPostPattern.test(screenerSrc),
      "AF-7: AddButton must not call raw fetch('/api/symbols'). " +
      "Rusty: replace with addSymbol() from portfolio-api.ts."
    );
  });

  it("AF-8: AddButton performs flag toggle via PUT after addSymbol step", () => {
    // The two-step pattern: step 1 addSymbol(), step 2 PUT flag
    assert.ok(
      screenerSrc.includes("PUT") &&
      (screenerSrc.includes("cash_secured_put") || screenerSrc.includes("buy_tracker")),
      "AF-8: AddButton must perform a separate PUT to toggle the flag after addSymbol()"
    );

    // The PUT must be to /api/symbols/{symbol}, not bundled into addSymbol
    assert.ok(
      screenerSrc.match(/method:\s*["']PUT["']/),
      "AF-8: AddButton must use method: 'PUT' for the flag toggle step"
    );
  });

  it("AF-9: addSymbol create body does not include tracker flags", () => {
    // Find the addSymbol call in the screener source
    const addSymbolIdx = screenerSrc.indexOf("await addSymbol(");
    assert.ok(addSymbolIdx !== -1, "await addSymbol( not found");

    // Find the matching closing paren for the addSymbol call
    let depth = 0;
    let i = screenerSrc.indexOf("(", addSymbolIdx + "await addSymbol".length);
    const start = i;
    while (i < screenerSrc.length) {
      if (screenerSrc[i] === "(") depth++;
      if (screenerSrc[i] === ")") { depth--; if (depth === 0) break; }
      i++;
    }
    const addSymbolCallSrc = screenerSrc.slice(start, i + 1);

    // The create body must NOT contain cash_secured_put, buy_tracker, covered_call
    assert.ok(
      !addSymbolCallSrc.includes("cash_secured_put"),
      "AF-9: addSymbol create body must not include cash_secured_put (§2.6: flags default off)"
    );
    assert.ok(
      !addSymbolCallSrc.includes("buy_tracker"),
      "AF-9: addSymbol create body must not include buy_tracker (§2.6: flags default off)"
    );
    assert.ok(
      !addSymbolCallSrc.includes("covered_call"),
      "AF-9: addSymbol create body must not include covered_call (§2.6: flags default off)"
    );
  });

  it("AF-10: addSymbol create body contains canonical shape fields", () => {
    const addSymbolIdx = screenerSrc.indexOf("await addSymbol(");
    assert.ok(addSymbolIdx !== -1);

    // The call site should pass ticker, exchange_mic, company_name, listing_currency
    const nearCall = screenerSrc.slice(addSymbolIdx, addSymbolIdx + 600);
    assert.ok(
      nearCall.includes("ticker"),
      "AF-10: addSymbol create body must include ticker"
    );
    assert.ok(
      nearCall.includes("exchange_mic"),
      "AF-10: addSymbol create body must include exchange_mic"
    );
    assert.ok(
      nearCall.includes("company_name"),
      "AF-10: addSymbol create body must include company_name"
    );
    assert.ok(
      nearCall.includes("listing_currency"),
      "AF-10: addSymbol create body must include listing_currency"
    );
  });
});

// ── AF-11..13: toExchangeMic helper ──────────────────────────────────────────

describe("AF-11..13: toExchangeMic exchange-to-MIC helper in DgiScreenerView", () => {
  it("AF-11: toExchangeMic is exported or defined in DgiScreenerView", () => {
    assert.ok(
      screenerSrc.includes("toExchangeMic"),
      "AF-11: toExchangeMic function must exist in DgiScreenerView.tsx"
    );
  });

  it("AF-12: toExchangeMic maps NASDAQ to XNAS", () => {
    // Extract the function body
    const fnIdx = screenerSrc.indexOf("function toExchangeMic") !== -1
      ? screenerSrc.indexOf("function toExchangeMic")
      : screenerSrc.indexOf("toExchangeMic");
    assert.ok(fnIdx !== -1);
    const fnEnd = screenerSrc.indexOf("}", fnIdx + 50) + 1;
    const fnBody = screenerSrc.slice(fnIdx, fnEnd);
    assert.ok(
      fnBody.includes("XNAS"),
      "AF-12: toExchangeMic must map NASDAQ-related exchanges to XNAS"
    );
    assert.ok(
      fnBody.includes("NASDAQ") || fnBody.includes("NMS"),
      "AF-12: toExchangeMic must handle NASDAQ/NMS exchange strings"
    );
  });

  it("AF-13: toExchangeMic fallback is XNYS", () => {
    const fnIdx = screenerSrc.indexOf("function toExchangeMic") !== -1
      ? screenerSrc.indexOf("function toExchangeMic")
      : screenerSrc.indexOf("toExchangeMic");
    assert.ok(fnIdx !== -1);
    const fnEnd = screenerSrc.indexOf("}", fnIdx + 50) + 1;
    const fnBody = screenerSrc.slice(fnIdx, fnEnd);
    assert.ok(
      fnBody.includes("XNYS"),
      "AF-13: toExchangeMic must use XNYS as the safe US fallback"
    );
  });

  // Inline mirror for deterministic logic tests
  function toExchangeMic(exchange) {
    const ex = (exchange ?? "").toUpperCase().replace(/\s+/g, "");
    if (ex.startsWith("NASDAQ") || ex === "NMS" || ex === "NGM" || ex === "NCM") return "XNAS";
    if (ex === "AMEX" || ex === "NYSEAMERICAN" || ex === "ARCA" || ex === "NYSEARCA") return "XASE";
    return "XNYS";
  }

  it("AF-12a: toExchangeMic('NASDAQ') → XNAS", () => {
    assert.equal(toExchangeMic("NASDAQ"), "XNAS");
  });

  it("AF-12b: toExchangeMic('NMS') → XNAS", () => {
    assert.equal(toExchangeMic("NMS"), "XNAS");
  });

  it("AF-12c: toExchangeMic('NASDAQ Global Select') → XNAS", () => {
    assert.equal(toExchangeMic("NASDAQ Global Select"), "XNAS");
  });

  it("AF-13a: toExchangeMic('NYSE') → XNYS", () => {
    assert.equal(toExchangeMic("NYSE"), "XNYS");
  });

  it("AF-13b: toExchangeMic(undefined) → XNYS (safe fallback)", () => {
    assert.equal(toExchangeMic(undefined), "XNYS");
  });

  it("AF-13c: toExchangeMic('AMEX') → XASE", () => {
    assert.equal(toExchangeMic("AMEX"), "XASE");
  });
});

// ── AF-15: No other component raw-posts to /api/symbols ──────────────────────

describe("AF-15: No direct POST to /api/symbols in screener", () => {
  it("AF-15a: DgiScreenerView has no raw POST fetch to /api/symbols", () => {
    // This pattern would indicate a regression (the old bad pattern)
    const rawPost =
      screenerSrc.includes('fetch("/api/symbols"') ||
      screenerSrc.includes("fetch('/api/symbols'") ||
      screenerSrc.includes('"/api/symbols", {\n') ||
      screenerSrc.includes("'/api/symbols', {");
    assert.ok(
      !rawPost,
      "AF-15: DgiScreenerView must not use raw fetch to POST /api/symbols. " +
      "Use addSymbol() from portfolio-api instead."
    );
  });
});
