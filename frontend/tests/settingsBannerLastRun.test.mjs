import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const view = fs.readFileSync(
  path.join(root, "src/components/SettingsConfigView.tsx"),
  "utf8",
);
const bff = fs.readFileSync(
  path.join(root, "src/app/api/settings/config/route.ts"),
  "utf8",
);
const page = fs.readFileSync(
  path.join(root, "src/app/settings/config/page.tsx"),
  "utf8",
);
const dashboardPage = fs.readFileSync(
  path.join(root, "src/app/dashboard/page.tsx"),
  "utf8",
);
const autoRefresh = fs.readFileSync(
  path.join(root, "src/components/AutoRefresh.tsx"),
  "utf8",
);
const dashboardTypes = fs.readFileSync(
  path.join(root, "src/types/dashboard.ts"),
  "utf8",
);

test("Last Run renders Never only for an empty backend value", () => {
  assert.match(view, /\{last \|\| "Never"\}/);
  assert.doesNotMatch(view, /lastIso \|\| "Never"/);
});

test("banner Run Now applies the completed response without polling", () => {
  assert.match(view, /key === "banner" && data\.banner_last_run_iso/);
  assert.match(view, /banner_last_run:\s*data\.banner_last_run/);
  assert.match(view, /banner_last_run_iso:\s*data\.banner_last_run_iso/);
  assert.doesNotMatch(view, /RUN_REFRESH_ATTEMPTS|RUN_REFRESH_INTERVAL_MS|setTimeout/);
  assert.doesNotMatch(view, /\/api\/settings\/config\?run=/);
});

test("non-banner scheduler controls retain single-request behavior", () => {
  assert.doesNotMatch(view, /RUN_TIME_FIELDS/);
  assert.equal((view.match(/await fetch\(endpoint, \{ method: "POST" \}\)/g) || []).length, 1);
  assert.match(view, /const msg = data\.status \|\| data\.message/);
});

test("settings server and BFF reads opt out of stale fetch caching", () => {
  assert.match(bff, /apiFetch<unknown>\("\/api\/settings\/config", \{ cache: "no-store" \}\)/);
  assert.match(page, /apiFetch<SettingsConfig>\("\/api\/settings\/config", \{ cache: "no-store" \}\)/);
});

test("dashboard refresh signature includes persisted banner generation", () => {
  assert.match(dashboardTypes, /banner_generated_at\?: string \| null/);
  assert.match(autoRefresh, /b: data\.banner_generated_at \?\? null/);
  assert.match(dashboardPage, /apiFetch<DashboardData>\("\/api\/dashboard", \{ cache: "no-store" \}\)/);
  assert.equal((autoRefresh.match(/^\s*router\.refresh\(\);/gm) || []).length, 1);
});
