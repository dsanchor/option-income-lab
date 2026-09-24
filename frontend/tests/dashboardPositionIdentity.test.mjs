import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = (path) => readFileSync(join(root, path), "utf8");
const tables = source("src/components/DashboardAgentTables.tsx");
const types = source("src/types/dashboard.ts");

describe("dashboard position monitor identity contract", () => {
  it("renders every backend position row without symbol-level deduplication", () => {
    assert.match(tables, /agent\.rows\.map\(\(row\) =>/);
    assert.match(tables, /key=\{row\.key\}/);
    assert.doesNotMatch(tables, /new Map\([^)]*row\.symbol/);
  });

  it("preserves stable position identity on monitor rows", () => {
    assert.match(types, /position_id\?: string \| null/);
    assert.match(tables, /data-position-id=\{row\.position_id \|\| undefined\}/);
  });

  it("renders missing per-position monitor data explicitly", () => {
    assert.match(tables, /row\.assignment_risk \?/);
    assert.match(tables, /row\.delta != null \? row\.delta\.toFixed\(2\) : "—"/);
    assert.match(tables, /row\.pnl_pct != null \?/);
    assert.match(tables, /if \(paused \|\| !items \|\| items\.length === 0\) return <>—<\/>/);
  });

  it("keys recent activities by activity identity when available", () => {
    assert.match(tables, /key=\{act\.id \|\| `\$\{act\.timestamp \|\| "activity"\}-\$\{i\}`\}/);
  });
});
