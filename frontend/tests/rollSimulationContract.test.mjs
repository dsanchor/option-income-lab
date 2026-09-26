import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = (path) => readFileSync(join(root, path), "utf8");
const detail = source("src/components/PositionDetail.tsx");
const route = source("src/app/api/symbols/[symbol]/positions/[positionId]/roll-simulation/route.ts");

describe("simulate a roll frontend contract", () => {
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
