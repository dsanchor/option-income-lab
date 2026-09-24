import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = (path) => readFileSync(join(root, path), "utf8");
const trigger = source("src/components/TriggerButton.tsx");
const tables = source("src/components/DashboardAgentTables.tsx");
const autoRefresh = source("src/components/AutoRefresh.tsx");
const types = source("src/types/dashboard.ts");

describe("dashboard trigger run-status contract", () => {
  it("requires a run ID before polling and never marks POST acceptance complete", () => {
    assert.match(trigger, /typeof data\.run_id !== "string"/);
    assert.match(trigger, /pollRun\(data\.run_id/);
    assert.doesNotMatch(trigger, /data\.status === "triggered" \? "done"/);
  });

  it("resolves success and failure from the exact run-ID record", () => {
    assert.match(trigger, /data\.runs\?\.\[runId\]/);
    assert.match(trigger, /run\?\.status === "succeeded"/);
    assert.match(trigger, /run\?\.status === "failed"/);
    assert.match(trigger, /run\.error \?\? "Run failed"/);
    assert.match(trigger, /router\.refresh\(\)/);
  });

  it("sends complete position identity from the clicked monitor row", () => {
    assert.match(trigger, /\.\.\.position/);
    assert.match(tables, /position_id: row\.position_id/);
    assert.match(tables, /account_id: row\.account_id/);
    assert.match(tables, /contract_id: row\.contract_id/);
    assert.match(tables, /instrument_id: row\.instrument_id/);
    assert.match(tables, /is_paper: row\.is_paper/);
    assert.match(types, /account_id\?: string \| null/);
    assert.match(types, /is_paper\?: boolean/);
  });

  it("preserves 409 handling, double-click protection, timeout, and cleanup", () => {
    assert.match(trigger, /pendingRef\.current/);
    assert.match(trigger, /res\.status === 409/);
    assert.match(trigger, /data\.status === "disabled"/);
    assert.match(trigger, /finish\("deactivated", "Deactivated globally"\)/);
    assert.match(trigger, /RUN_TIMEOUT_MS/);
    assert.match(trigger, /abortRef\.current\?\.abort\(\)/);
    assert.match(trigger, /clearTimeout\(pollTimerRef\.current\)/);
  });

  it("renders globally disabled controls as native and ARIA disabled", () => {
    assert.match(trigger, /globallyDisabled\?: boolean/);
    assert.match(trigger, /disabled=\{\s*globallyDisabled/);
    assert.match(
      trigger,
      /aria-disabled=\{globallyDisabled \|\| status === "deactivated" \|\| undefined\}/,
    );
    assert.match(trigger, /Deactivated globally/);
    assert.equal(
      tables.match(/globallyDisabled=\{globallyDisabled\}/g)?.length,
      2,
    );
  });

  it("includes aggregated execution state in the auto-refresh signature", () => {
    assert.match(autoRefresh, /s: data\.agent_statuses \?\? \{\}/);
    assert.match(autoRefresh, /g: data\.monitor_agent_enabled \?\? \{\}/);
  });

  it("shares typed run and dashboard status payload contracts", () => {
    assert.match(types, /interface DashboardRunStatus/);
    assert.match(types, /runs\?: Record<string, DashboardRunStatus>/);
    assert.match(types, /agent_statuses\?: Record<string, DashboardRunStatus>/);
  });
});
