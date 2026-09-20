import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const __dir = dirname(fileURLToPath(import.meta.url));
const root = join(__dir, "..");
const helperSource = readFileSync(join(root, "src/lib/holdingPnl.ts"), "utf8");
const cardSource = readFileSync(
  join(root, "src/components/PortfolioHoldingsCard.tsx"),
  "utf8",
);
const typesSource = readFileSync(join(root, "src/types/symbol-detail.ts"), "utf8");
const compiledHelper = ts.transpileModule(helperSource, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const helperUrl = `data:text/javascript;base64,${Buffer.from(compiledHelper).toString("base64")}`;
const { formatHoldingPnl } = await import(helperUrl);

function display(currentShares, unrealizedPnlEur, unrealizedPnlPct) {
  return formatHoldingPnl({ currentShares, unrealizedPnlEur, unrealizedPnlPct });
}

describe("Symbol holding unrealized P/L display", () => {
  it("formats positive P/L with a positive tone and explicit gain text", () => {
    const result = display("10", "125.50", "12.55");
    assert.equal(result.tone, "positive");
    assert.match(result.absolute, /^\+/);
    assert.equal(result.percentage, "+12.55%");
    assert.match(result.ariaLabel, /^Unrealized gain:/);
  });

  it("formats negative P/L with a negative tone and explicit loss text", () => {
    const result = display("10", "-87.25", "-8.73");
    assert.equal(result.tone, "negative");
    assert.match(result.absolute, /^-/);
    assert.equal(result.percentage, "-8.73%");
    assert.match(result.ariaLabel, /^Unrealized loss:/);
  });

  it("formats exact zero P/L neutrally without a positive sign", () => {
    const result = display("10", "0.00", "0.00");
    assert.equal(result.tone, "neutral");
    assert.doesNotMatch(result.absolute, /^\+/);
    assert.equal(result.percentage, "0.00%");
    assert.match(result.ariaLabel, /^No unrealized gain or loss:/);
  });

  it("renders unavailable and non-finite backend values as em dashes", () => {
    for (const result of [
      display("10", null, null),
      display("10", "NaN", "NaN"),
      display("10", "Infinity", "Infinity"),
    ]) {
      assert.equal(result.available, false);
      assert.equal(result.absolute, "—");
      assert.equal(result.percentage, "—");
      assert.equal(result.ariaLabel, "Unrealized P/L unavailable");
    }
  });

  it("keeps absolute P/L but renders an unavailable percentage for a zero cost denominator", () => {
    const result = display("10", "1000.00", null);
    assert.equal(result.available, true);
    assert.match(result.absolute, /^\+/);
    assert.equal(result.percentage, "—");
    assert.match(result.ariaLabel, /percentage unavailable$/);
  });

  it("suppresses P/L for closed and zero-share holdings", () => {
    for (const shares of ["0", "-2"]) {
      const result = display(shares, "100.00", "10.00");
      assert.equal(result.available, false);
      assert.equal(result.absolute, "—");
      assert.equal(result.percentage, "—");
    }
  });

  it("maps the backend fields directly and uses accessible signed tone conventions", () => {
    for (const field of ["current_value_eur", "unrealized_pnl_eur", "unrealized_pnl_pct"]) {
      assert.match(typesSource, new RegExp(`${field}\\?: string \\| null`));
    }
    assert.match(cardSource, /portfolio\.unrealized_pnl_eur/);
    assert.match(cardSource, /portfolio\.unrealized_pnl_pct/);
    assert.match(cardSource, /aria-label=\{pnl\.ariaLabel\}/);
    assert.match(cardSource, /positive: "text-accent-green"/);
    assert.match(cardSource, /negative: "text-accent-red"/);
    assert.match(cardSource, /neutral: "text-text-muted"/);
  });
});
