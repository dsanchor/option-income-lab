/**
 * Regression tests — Rev 6 portfolio/watchlist classification.
 *
 * Supersedes symbolsOverviewIncludeZeroPortfolio.test.mjs (deleted). That
 * file guarded the OLD contract: the backend hid auto-enrolled zero-share
 * ("historical") rows unless the caller sent `include_zero_portfolio=true`.
 *
 * Rev 6 rule (current): "portfolio" = the active holdings snapshot has an
 * entry for the ticker at all (real equity trade history), regardless of
 * current share count. Zero/negative-share portfolio rows are marked
 * `is_historical` and hidden by default in the UI via a "Hide historical"
 * toggle scoped to the Portfolio section (see SymbolsTable.tsx), but the
 * API itself never hides them and never sends/needs `include_zero_portfolio`.
 * "watchlist" = no holdings entry at all (option-only symbols, or symbols
 * whose only equity movements were soft-deleted/SUPERSEDED/VOIDED).
 *
 * These tests confirm:
 *   1. `src/app/symbols/page.tsx` no longer requests the now-removed
 *      `include_zero_portfolio=true` query param (a stale caller would be
 *      silently ignored by the backend rather than erroring loudly).
 *   2. `src/app/api/symbols/overview/route.ts` still forwards query strings
 *      generically (harmless — no special-casing needed for this rule).
 *
 * Run with: node --test frontend/tests/symbolsOverviewRev6Visibility.test.mjs
 */

import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { register } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const srcRoot = new URL("../src/", import.meta.url).href;
const hookSource = `
  import { existsSync } from "node:fs";
  import { fileURLToPath } from "node:url";

  export async function resolve(specifier, context, nextResolve) {
    if (specifier === "next/server") {
      return { url: "shim:next-server", shortCircuit: true };
    }
    if (specifier.startsWith("@/")) {
      const rel = specifier.slice(2);
      const base = new URL(${JSON.stringify(srcRoot)});
      for (const ext of ["", ".ts", ".tsx"]) {
        const candidate = new URL(rel + ext, base);
        if (existsSync(fileURLToPath(candidate))) {
          return { url: candidate.href, shortCircuit: true };
        }
      }
    }
    return nextResolve(specifier, context);
  }

  export async function load(url, context, nextLoad) {
    if (url === "shim:next-server") {
      return {
        format: "module",
        shortCircuit: true,
        source: "export const NextResponse = { json: (body, init) => ({ status: (init && init.status) || 200, body }) };",
      };
    }
    return nextLoad(url, context);
  }
`;
register("data:text/javascript," + encodeURIComponent(hookSource), import.meta.url);

const routeModulePromise = import("../src/app/api/symbols/overview/route.ts");

function withMockedFetch(responder, fn) {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push(String(url));
    return responder(String(url), init);
  };
  return Promise.resolve()
    .then(() => fn(calls))
    .finally(() => {
      globalThis.fetch = original;
    });
}

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

describe("BFF route /api/symbols/overview still forwards query strings generically", () => {
  test("RQ-1: no query string forwards no query to the backend", async () => {
    const { GET } = await routeModulePromise;
    await withMockedFetch(
      () => jsonResponse({ symbols: [] }),
      async (calls) => {
        const res = await GET(new Request("http://localhost/api/symbols/overview"));
        assert.equal(calls.length, 1);
        assert.equal(calls[0], "http://localhost:8000/api/symbols/overview");
        assert.equal(res.status, 200);
      },
    );
  });

  test("RQ-2: any query string is forwarded verbatim (route.ts has no special-casing)", async () => {
    const { GET } = await routeModulePromise;
    await withMockedFetch(
      () => jsonResponse({ symbols: [] }),
      async (calls) => {
        await GET(new Request("http://localhost/api/symbols/overview?foo=bar"));
        assert.equal(calls.length, 1);
        assert.equal(calls[0], "http://localhost:8000/api/symbols/overview?foo=bar");
      },
    );
  });
});

describe("Symbols page no longer requests the removed include_zero_portfolio flag", () => {
  // page.tsx renders JSX, which Node's native TS type-stripping cannot parse,
  // so it can't be import()-ed directly in this plain-Node test harness.
  const pageSrc = readFileSync(
    fileURLToPath(new URL("../src/app/symbols/page.tsx", import.meta.url)),
    "utf8",
  );

  test("PG-1: getData() no longer includes include_zero_portfolio=true (rev 6: never hidden)", () => {
    assert.ok(
      !pageSrc.includes("include_zero_portfolio"),
      "Rev 6 removed the include_zero_portfolio param entirely — backend always returns every symbol.",
    );
  });

  test("PG-2: page still renders exactly one SymbolsTable (single shared toolbar, two internal sections)", () => {
    const matches = pageSrc.match(/<SymbolsTable\b/g) || [];
    assert.equal(matches.length, 1, "expected exactly one <SymbolsTable /> usage");
  });
});
