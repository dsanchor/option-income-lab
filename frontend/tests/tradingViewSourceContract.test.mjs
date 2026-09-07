/**
 * tradingViewSourceContract.test.mjs — TradingView symbol source-contract tests.
 *
 * Ref: danny-tradingview-symbol-contract.md §2.4 (frontend migration), §2.5 (fail-closed)
 * Also: Rusty's corrected toExchangeMic (DGI screener US-options-only).
 *
 * Coverage:
 *   TVF-1   SymbolDetail type has tradingview_symbol: string | null field.
 *   TVF-2   TradingViewSymbolInfo accepts tvSymbol: string | null prop (not symbol+exchange).
 *   TVF-3   TradingViewSymbolInfo renders nothing when tvSymbol is null.
 *   TVF-4   TradingViewSymbolInfo uses .replace('-', ':') for widget symbol.
 *   TVF-5   RtChart accepts tvSymbol: string | null prop (not symbol+exchange).
 *   TVF-6   RtChart renders nothing when tvSymbol is null (fail-closed, §2.5).
 *   TVF-7   RtChart uses .replace('-', ':') for widget config.
 *   TVF-8   symbols/[symbol]/page.tsx passes tvSymbol={d.tradingview_symbol}.
 *   TVF-9   No MIC→TradingView mapping table in TypeScript/frontend code.
 *   TVF-10  No old client-side tvSymbol assembly from (exchange, symbol).
 *   DGI-1   toExchangeMic('AMEX') → null (not XASE).
 *   DGI-2   toExchangeMic('ARCA'/'NYSEARCA') → null.
 *   DGI-4   toExchangeMic(unknown/undefined/''/whitespace) → null (not an XNYS/XNAS fallback).
 *   DGI-4b  Structural: the function's trailing/unconditional statement is `return null;`.
 *   DGI-4c  Structural: every literal XNYS/XNAS return is guarded by its own `if` on the same line
 *           (distinguishes a legitimate guarded branch, e.g. `if (ex === "NYSE") return "XNYS";`,
 *           from an unconditional default fallback — see 2026-09-07 DGI-4 rejection below).
 *   DGI-7   toExchangeMic('NASDAQ' and aliases) → XNAS (still valid).
 *   DGI-8   toExchangeMic('NYSE') → XNYS (still valid).
 *   DGI-9   AddButton's null-MIC guard precedes addSymbol/PUT calls in source order.
 *
 * DGI-1, DGI-2, DGI-4, DGI-7, DGI-8 execute the REAL toExchangeMic function body
 * (extracted from DgiScreenerView.tsx via balanced-brace parsing and run with
 * `new Function`) rather than pattern-matching its text — this proves actual
 * behavior instead of assuming it from source substrings.
 *
 * 2026-09-07 (Reuben, independent revision — Basher locked out): the original
 * DGI-4 used a naive whole-file substring scan for `return "XNYS"`, which
 * false-flagged the legitimate, explicitly-guarded `if (ex === "NYSE") return
 * "XNYS";` branch as an unsafe default fallback. Danny confirmed the product
 * code (toExchangeMic in DgiScreenerView.tsx) was already correct; only this
 * test was defective. Fixed by replacing the substring scan with real
 * execution (DGI-4) plus targeted structural checks (DGI-4b/DGI-4c) that can
 * tell a guarded branch from an unconditional one. No product code changed.
 *
 * TVF-2..10 WILL FAIL until Rusty migrates widget components.
 *
 * Run: node --test frontend/tests/tradingViewSourceContract.test.mjs
 */

import { readFileSync } from "fs";
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");

const symbolDetailTypeSrc = readFileSync(
  join(root, "src/types/symbol-detail.ts"), "utf8"
);
const tvInfoSrc = readFileSync(
  join(root, "src/components/TradingViewSymbolInfo.tsx"), "utf8"
);
const rtChartSrc = readFileSync(
  join(root, "src/components/RtChart.tsx"), "utf8"
);
const symbolPageSrc = readFileSync(
  join(root, "src/app/symbols/[symbol]/page.tsx"), "utf8"
);
const screenerSrc = readFileSync(
  join(root, "src/components/DgiScreenerView.tsx"), "utf8"
);

// ── TVF-1: SymbolDetail type ──────────────────────────────────────────────────

describe("TVF-1: SymbolDetail type includes tradingview_symbol", () => {
  it("TVF-1a: tradingview_symbol field in SymbolDetail", () => {
    assert.ok(
      symbolDetailTypeSrc.includes("tradingview_symbol"),
      "TVF-1: SymbolDetail must include tradingview_symbol: string | null. " +
      "Rusty: add field to src/types/symbol-detail.ts."
    );
  });

  it("TVF-1b: tradingview_symbol typed as string | null (not just string)", () => {
    const idx = symbolDetailTypeSrc.indexOf("tradingview_symbol");
    if (idx === -1) {
      assert.fail("TVF-1b: tradingview_symbol field not found in SymbolDetail");
    }
    const line = symbolDetailTypeSrc.slice(idx, idx + 80).split("\n")[0];
    assert.ok(
      line.includes("null"),
      `TVF-1b: tradingview_symbol must be 'string | null', not just string. Got: ${line}`
    );
  });
});

// ── TVF-2..4: TradingViewSymbolInfo.tsx ──────────────────────────────────────

describe("TVF-2..4: TradingViewSymbolInfo uses single tvSymbol prop", () => {
  it("TVF-2: TradingViewSymbolInfo has tvSymbol prop (not symbol+exchange)", () => {
    assert.ok(
      tvInfoSrc.includes("tvSymbol"),
      "TVF-2: TradingViewSymbolInfo must accept tvSymbol: string | null prop. " +
      "Rusty: refactor from {symbol, exchange} to {tvSymbol: string | null}."
    );
  });

  it("TVF-2: TradingViewSymbolInfo does NOT build tvSymbol client-side from exchange+symbol", () => {
    // The old pattern: `exchange ? `${exchange}:${symbol}` : symbol`
    const oldBuild = tvInfoSrc.match(/exchange\s*\?\s*`\$\{exchange\}:\$\{symbol\}`/);
    assert.ok(
      !oldBuild,
      "TVF-2: TradingViewSymbolInfo must not build tvSymbol from exchange+symbol props. " +
      "Use the backend-provided tvSymbol. Old pattern found: " + (oldBuild && oldBuild[0])
    );
  });

  it("TVF-3: TradingViewSymbolInfo has null guard — renders nothing when tvSymbol is null", () => {
    const hasNullGuard =
      tvInfoSrc.includes("!tvSymbol") ||
      tvInfoSrc.includes("tvSymbol === null") ||
      tvInfoSrc.includes("tvSymbol == null") ||
      (tvInfoSrc.includes("return null") && tvInfoSrc.includes("tvSymbol"));
    assert.ok(
      hasNullGuard,
      "TVF-3: TradingViewSymbolInfo must render nothing when tvSymbol is null. " +
      "Add: if (!tvSymbol) return null; (§2.5 fail-closed)"
    );
  });

  it("TVF-4: TradingViewSymbolInfo uses .replace('-', ':') for widget config", () => {
    // The component must do the mechanical transform
    const hasTransform =
      tvInfoSrc.includes(".replace") && (
        tvInfoSrc.includes('"-"') && tvInfoSrc.includes('":"') ||
        tvInfoSrc.includes("'-'") && tvInfoSrc.includes("':'")
      );
    assert.ok(
      hasTransform,
      "TVF-4: TradingViewSymbolInfo must call tvSymbol.replace('-', ':') for the widget symbol."
    );
  });
});

// ── TVF-5..7: RtChart.tsx ─────────────────────────────────────────────────────

describe("TVF-5..7: RtChart uses single tvSymbol prop", () => {
  it("TVF-5: RtChart has tvSymbol prop (not symbol+exchange)", () => {
    assert.ok(
      rtChartSrc.includes("tvSymbol"),
      "TVF-5: RtChart must accept tvSymbol: string | null prop. Rusty: refactor."
    );
  });

  it("TVF-5: RtChart does NOT build tvSymbol client-side from exchange+symbol", () => {
    const oldBuild = rtChartSrc.match(/exchange\s*\?\s*`\$\{exchange\}:\$\{symbol\}`/);
    assert.ok(
      !oldBuild,
      "TVF-5: RtChart must not build tvSymbol from exchange+symbol props. " +
      "Old pattern found: " + (oldBuild && oldBuild[0])
    );
  });

  it("TVF-6: RtChart has null guard — renders nothing when tvSymbol is null", () => {
    const hasNullGuard =
      rtChartSrc.includes("!tvSymbol") ||
      rtChartSrc.includes("tvSymbol === null") ||
      (rtChartSrc.includes("return null") && rtChartSrc.includes("tvSymbol"));
    assert.ok(
      hasNullGuard,
      "TVF-6: RtChart must not render the chart when tvSymbol is null (§2.5 fail-closed)."
    );
  });

  it("TVF-7: RtChart uses .replace('-', ':') for widget config symbol", () => {
    const hasTransform =
      rtChartSrc.includes(".replace") && (
        rtChartSrc.includes('"-"') && rtChartSrc.includes('":"') ||
        rtChartSrc.includes("'-'") && rtChartSrc.includes("':'")
      );
    assert.ok(
      hasTransform,
      "TVF-7: RtChart must call tvSymbol.replace('-', ':') for the chart widget symbol."
    );
  });
});

// ── TVF-8: page.tsx ───────────────────────────────────────────────────────────

describe("TVF-8: page.tsx passes tvSymbol from backend field", () => {
  it("TVF-8a: page.tsx references d.tradingview_symbol", () => {
    assert.ok(
      symbolPageSrc.includes("tradingview_symbol"),
      "TVF-8: page.tsx must use d.tradingview_symbol to pass to TradingViewSymbolInfo/RtChart."
    );
  });

  it("TVF-8b: page.tsx passes tvSymbol prop to widget components", () => {
    assert.ok(
      symbolPageSrc.includes("tvSymbol"),
      "TVF-8: page.tsx must pass tvSymbol= to TradingViewSymbolInfo and RtChart."
    );
  });
});

// ── TVF-9: no frontend MIC mapping table ─────────────────────────────────────

describe("TVF-9: no MIC→TradingView mapping table in frontend code", () => {
  it("TVF-9: MIC_TO_TRADINGVIEW not in widget or page TypeScript", () => {
    // Reject actual mapping table identifiers or XMAD/BME as string literals (keys).
    // Allow "BME-ACS" / "BME:ACS" in comments that illustrate the hyphen→colon transform.
    const tablePattern = /MIC_TO_TRADINGVIEW|["']\s*XMAD\s*["']|["']\s*BME\s*["']\s*:/;
    const sources = { tvInfoSrc, rtChartSrc, symbolPageSrc };
    for (const [name, src] of Object.entries(sources)) {
      assert.ok(
        !tablePattern.test(src),
        `TVF-9: ${name} must not contain a MIC→TradingView mapping table (§2.7: backend-only).`
      );
    }
  });
});

// ── TVF-10: old assembly pattern absent ──────────────────────────────────────

describe("TVF-10: old client-side tvSymbol assembly absent", () => {
  it("TVF-10a: TradingViewSymbolInfo.tsx no longer has exchange?`${exchange}:${symbol}` pattern", () => {
    const badPattern = /exchange\s*\?\s*`\$\{exchange\}:\$\{symbol\}`/;
    assert.ok(
      !badPattern.test(tvInfoSrc),
      "TVF-10: TradingViewSymbolInfo.tsx still has old exchange+symbol assembly. Rusty: remove it."
    );
  });

  it("TVF-10b: RtChart.tsx no longer has exchange?`${exchange}:${symbol}` pattern", () => {
    const badPattern = /exchange\s*\?\s*`\$\{exchange\}:\$\{symbol\}`/;
    assert.ok(
      !badPattern.test(rtChartSrc),
      "TVF-10: RtChart.tsx still has old exchange+symbol assembly. Rusty: remove it."
    );
  });
});

// ── DGI-1..9: toExchangeMic corrected to reject non-eligible exchanges ────────

describe("DGI-1..9: toExchangeMic rejects non-eligible exchanges (Rusty correction)", () => {
  // ── Extract the REAL toExchangeMic body and execute it ─────────────────────
  //
  // The previous DGI-4 test used a naive whole-file substring scan for
  // `return "XNYS"`, which falsely flagged the legitimate, explicitly-guarded
  // `if (ex === "NYSE") return "XNYS";` branch as an "unsafe default
  // fallback". A substring scan cannot distinguish a guarded branch from an
  // unconditional one, and it also cannot see whether *other* callers of the
  // function elsewhere in the file happen to contain that same literal.
  //
  // Instead we isolate the exact function body via balanced-brace parsing
  // (robust to reformatting/whitespace changes) and execute the real
  // extracted body as a plain function, so behavior is proven by actually
  // calling the production logic — not by pattern-matching its text.
  const fnNameIdx = screenerSrc.indexOf("function toExchangeMic");
  assert.ok(fnNameIdx !== -1, "toExchangeMic function not found in DgiScreenerView.tsx");

  const openBraceIdx = screenerSrc.indexOf("{", fnNameIdx);
  assert.ok(openBraceIdx !== -1, "toExchangeMic: no opening brace found");

  // Balanced-brace scan from the function's opening brace to its matching
  // closing brace. The function body contains no nested string/template
  // literals that themselves contain braces, so a plain depth counter is
  // sufficient and avoids pulling in unrelated code below it.
  let depth = 0;
  let closeBraceIdx = -1;
  for (let i = openBraceIdx; i < screenerSrc.length; i++) {
    if (screenerSrc[i] === "{") depth++;
    else if (screenerSrc[i] === "}") {
      depth--;
      if (depth === 0) { closeBraceIdx = i; break; }
    }
  }
  assert.ok(closeBraceIdx !== -1, "toExchangeMic: no matching closing brace found");

  const fnBody = screenerSrc.slice(openBraceIdx + 1, closeBraceIdx);
  // Body is plain JS (the TypeScript annotations live only in the signature,
  // which is excluded here), so it can be executed directly — no transpile
  // step needed.
  const toExchangeMic = new Function("exchange", fnBody);

  const NYSE_MIC = "XNYS";
  const NASDAQ_MIC = "XNAS";

  it("DGI-8: known NYSE resolves to XNYS", () => {
    assert.equal(toExchangeMic("NYSE"), NYSE_MIC);
  });

  it("DGI-7: known Nasdaq aliases resolve to XNAS", () => {
    for (const alias of ["NASDAQ", "NASDAQGS", "NASDAQGM", "NASDAQCM", "NMS", "NGM", "NCM"]) {
      assert.equal(toExchangeMic(alias), NASDAQ_MIC, `expected ${alias} -> XNAS`);
    }
  });

  it("DGI-1: AMEX (NYSE American / XASE) resolves to null, not XASE or XNYS", () => {
    assert.equal(toExchangeMic("AMEX"), null);
  });

  it("DGI-2: ARCA / NYSEARCA resolve to null, not XASE or XNYS", () => {
    assert.equal(toExchangeMic("ARCA"), null);
    assert.equal(toExchangeMic("NYSEARCA"), null);
  });

  it("DGI-4: unknown, OTC, undefined, and empty-string exchanges resolve to null (no XNYS default)", () => {
    // This is the behavioral proof that closes the original gap: none of
    // these unmatched inputs may fall through to the NYSE branch.
    for (const input of ["OTC", "PINK", "FOREIGN", "XYZZY", undefined, "", "   "]) {
      assert.equal(
        toExchangeMic(input),
        null,
        `expected unresolvable exchange ${JSON.stringify(input)} -> null, not a silent XNYS/XNAS fallback`
      );
    }
  });

  it("DGI-4b (structural): the function's final fallback statement is an unconditional 'return null'", () => {
    // Confirms there is exactly one unconditional statement — the trailing
    // fallback — and that it returns null. Every other return in the body
    // must be reachable only through an `if` guard (checked next).
    const trailingStatement = fnBody.trim().split("\n").pop().trim();
    assert.equal(
      trailingStatement,
      "return null;",
      `toExchangeMic's final (unconditional) statement must be 'return null;'. Got: ${trailingStatement}`
    );
  });

  it("DGI-4c (structural): every 'XNYS'/'XNAS' return is textually guarded by its own 'if' on the same line", () => {
    // Distinguishes a guarded branch (`if (cond) return "XNYS";`) from an
    // unconditional/default one (`return "XNYS";` alone on its line) — the
    // exact ambiguity the naive whole-file scan could not resolve.
    const codeLines = fnBody.split("\n").map((l) => l.trim()).filter(Boolean);
    for (const line of codeLines) {
      if (line.includes('"XNYS"') || line.includes('"XNAS"')) {
        assert.ok(
          /^if\s*\(.+\)\s*return\s+["'](XNYS|XNAS)["'];?$/.test(line),
          `DGI-4c: line "${line}" returns an exchange MIC but is not guarded by an 'if' condition on the same line — ` +
          `this is the unconditional-default-fallback pattern that must not exist.`
        );
      }
    }
  });

  it("DGI-9: AddButton's null-guard for toExchangeMic precedes both the addSymbol call and the PUT fetch call", () => {
    // Order-based structural check (stronger than substring presence): the
    // `if (mic === null) { ... return; }` early-exit must appear in the
    // source *before* the addSymbol() and fetch() calls, proving those
    // network calls are unreachable on the null path for this straight-line
    // async function. Full behavioral rendering isn't possible — this repo
    // has no React component test harness (no jsdom/testing-library dep) to
    // mount AddButton and simulate a click.
    const addBtnIdx = screenerSrc.indexOf("function AddButton");
    assert.ok(addBtnIdx !== -1, "AddButton not found");

    const onClickIdx = screenerSrc.indexOf("async function onClick", addBtnIdx);
    assert.ok(onClickIdx !== -1, "AddButton.onClick not found");

    const micCallIdx = screenerSrc.indexOf("toExchangeMic(", onClickIdx);
    const nullGuardIdx = screenerSrc.indexOf("if (mic === null)", onClickIdx);
    const addSymbolIdx = screenerSrc.indexOf("await addSymbol(", onClickIdx);
    const fetchIdx = screenerSrc.indexOf("await fetch(", onClickIdx);

    assert.ok(micCallIdx !== -1, "onClick must call toExchangeMic(entry.exchange)");
    assert.ok(nullGuardIdx !== -1, "onClick must guard on `if (mic === null)`");
    assert.ok(addSymbolIdx !== -1, "onClick must call addSymbol on the resolved path");
    assert.ok(fetchIdx !== -1, "onClick must call fetch (PUT) on the resolved path");

    assert.ok(
      micCallIdx < nullGuardIdx && nullGuardIdx < addSymbolIdx && addSymbolIdx < fetchIdx,
      "DGI-9: expected source order toExchangeMic() -> if (mic === null) guard -> addSymbol() -> fetch(), " +
      `got indices mic=${micCallIdx} guard=${nullGuardIdx} addSymbol=${addSymbolIdx} fetch=${fetchIdx}`
    );

    // The null-guard block itself must return (early-exit) before reaching
    // addSymbol/fetch — not merely set a label and fall through.
    const guardBlock = screenerSrc.slice(nullGuardIdx, addSymbolIdx);
    assert.ok(
      /return\s*;/.test(guardBlock),
      "DGI-9: the `if (mic === null)` block must `return;` early, preventing addSymbol/PUT from executing"
    );
  });
});
