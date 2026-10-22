import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = (path) => readFileSync(join(root, path), "utf8");
const detail = source("src/components/PositionDetail.tsx");
const route = source("src/app/api/symbols/[symbol]/positions/[positionId]/roll-simulation/route.ts");
const require = createRequire(import.meta.url);
const ts = require("typescript");
const { createElement } = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const countModule = { exports: {} };
const countCode = ts.transpileModule(source("src/components/PositionContractCounts.tsx"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
new Function("require", "module", "exports", countCode)(require, countModule, countModule.exports);
const renderCounts = (position) => renderToStaticMarkup(createElement(countModule.exports.default, { position }));

describe("simulate a roll frontend contract", () => {
  it("renders original totals separately from partial and terminal remaining counts", () => {
    const resolved = (count, source) => ({ count, source, status: "resolved", warnings: [] });
    const partial = renderCounts({
      contract_counts: { total: resolved(5, "quantity"), remaining: resolved(2, "open_contracts") },
    });
    assert.match(partial, /Total: <span[^>]*>5<\/span>/);
    assert.match(partial, /Remaining: <span[^>]*>2<\/span>/);
    assert.match(partial, /Source: open_contracts/);
    const closed = renderCounts({
      contract_counts: { total: resolved(5, "contracts"), remaining: resolved(0, "terminal_status") },
    });
    assert.match(closed, /Remaining: <span[^>]*>0<\/span>/);
  });

  it("visibly preserves unknown/error and historical warnings without raw-value fallback", () => {
    const error = { count: null, status: "error", source: null, warnings: [], message: "Conflicting quantity aliases" };
    const unknown = renderCounts({ contracts: 99, contract_counts: { total: error, remaining: error } });
    assert.match(unknown, /Unknown/);
    assert.match(unknown, /Conflicting quantity aliases/);
    assert.doesNotMatch(unknown, />99</);
    assert.match(renderCounts({ contracts: 1 }), /Unknown/);
    const historical = { count: 1, status: "resolved", source: "legacy_implicit_one", warnings: ["Historical default"] };
    const markup = renderCounts({ contract_counts: { total: historical, remaining: historical } });
    assert.match(markup, /historical estimate/);
    assert.match(markup, /Historical default/);
    assert.match(markup, /Total: <span[^>]*>1<\/span>/);
  });

  it("uses the same backend-resolved total and remaining display in list and detail", () => {
    const counts = source("src/components/PositionContractCounts.tsx");
    const table = source("src/components/PositionsTable.tsx");
    assert.match(detail, /<PositionContractCounts position=\{position\}/);
    assert.match(table, /<PositionContractCounts position=\{p\}/);
    assert.match(table, />Contracts<\/th>/);
    assert.match(counts, /position\.contract_counts\?\.\[kind\]/);
    assert.match(counts, /"total", "remaining"/);
    assert.match(counts, /"Unknown"/);
    assert.match(counts, /value\?\.message/);
    assert.match(counts, /historical estimate/);
    assert.doesNotMatch(counts, /position\.(contracts|quantity|open_contracts|contracts_open)\b|\|\| 1|\?\? 1/);
    assert.doesNotMatch(detail, /typeof position\.contracts === "number"/);
  });

  it("renders immediately after Roll Scenarios for active exact positions", () => {
    const rollScenarios = detail.indexOf("🔄 Roll Scenarios");
    const simulator = detail.indexOf("Simulate a Roll", rollScenarios);
    const activeBlockEnd = detail.indexOf("{/* Delete */}", simulator);
    assert.ok(rollScenarios >= 0 && simulator > rollScenarios && activeBlockEnd > simulator);
    assert.match(detail, /positionId=\{posId\}/);
  });

  it("shows resolved full-position quantity and quantity-specific errors", () => {
    assert.match(detail, /quantity_source/);
    assert.match(detail, /Full position:/);
    assert.match(detail, /Position quantity unavailable/);
    assert.match(detail, /data\.quantity_source/);
  });

  it("sends exact position id, strike and expiration only on explicit action", () => {
    const simulatorStart = detail.indexOf("function RollSimulation(");
    const simulatorEnd = detail.indexOf("function EditableFinancialField(", simulatorStart);
    const simulatorSource = detail.slice(simulatorStart, simulatorEnd);
    assert.match(detail, /onClick=\{simulate\}/);
    assert.match(detail, /method: "POST"/);
    assert.match(detail, /target_strike: targetStrike/);
    assert.doesNotMatch(simulatorSource, /Number\(targetStrike\)/);
    assert.match(simulatorSource, /strikePattern = \/\^\\d\{1,9\}/);
    assert.match(simulatorSource, /type="text"/);
    assert.match(detail, /target_expiration: targetExpiration/);
    assert.match(detail, /positions\/\$\{encodeURIComponent\(positionId\)\}\/roll-simulation/);
    assert.doesNotMatch(simulatorSource, /useEffect\(/);
    assert.match(route, /positions\/\$\{encodeURIComponent\(positionId\)\}\/roll-simulation/);
  });

  it("covers precise chain, contract, midpoint, stale and carried states", () => {
    assert.match(detail, /loading \? "Calculating…" : "Simulate"/);
    assert.match(detail, /kind: "validation"/);
    assert.match(detail, /data\.code === "current_contract_not_found"/);
    assert.match(detail, /data\.code === "target_contract_not_found"/);
    assert.match(detail, /data\.code === "current_midpoint_unavailable"/);
    assert.match(detail, /data\.code === "target_midpoint_unavailable"/);
    assert.match(detail, /data\.code === "chain_unavailable"/);
    assert.match(detail, /Options chain unavailable\./);
    assert.match(detail, /Midpoint unavailable\./);
    assert.match(detail, /aria-invalid=\{error\?\.kind === "validation"\}/);
    assert.match(detail, /role="alert"/);
    assert.match(detail, /Current-contract quote is stale/);
    assert.match(detail, /carried last-known-good data/);
    assert.match(detail, /\.\.\.\(result\.chain_warnings \?\? \[\]\)/);
    assert.match(route, /code: "chain_unavailable"/);
  });

  it("renders credit, debit and even totals with midpoint disclaimer", () => {
    assert.match(detail, /"Estimated credit"/);
    assert.match(detail, /"Estimated debit"/);
    assert.match(detail, /"Estimated even"/);
    assert.match(detail, /Full position \(\$\{result\.contracts\} contracts\)/);
    assert.match(detail, /Informational midpoint estimate; commissions excluded; not an executable quote\./);
  });

  it("keeps same-symbol rows isolated and shows linked account labels", () => {
    assert.match(detail, /position\.linked_accounts\?\.filter/);
    assert.match(detail, /<AccountBadge key=\{accountId\}/);
    assert.match(detail, /positionId: string/);
  });
});
