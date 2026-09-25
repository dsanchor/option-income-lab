import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const dashboard = readFileSync(
  new URL("../src/app/dashboard/page.tsx", import.meta.url),
  "utf8",
);
const dashboardTypes = readFileSync(
  new URL("../src/types/dashboard.ts", import.meta.url),
  "utf8",
);
const settings = readFileSync(
  new URL("../src/components/SettingsConfigView.tsx", import.meta.url),
  "utf8",
);
const settingsTypes = readFileSync(
  new URL("../src/types/settings.ts", import.meta.url),
  "utf8",
);
const autoRefresh = readFileSync(
  new URL("../src/components/AutoRefresh.tsx", import.meta.url),
  "utf8",
);

test("removed dashboard banner has no UI, transport, config, or refresh signature", () => {
  for (const source of [
    dashboard,
    dashboardTypes,
    settings,
    settingsTypes,
    autoRefresh,
  ]) {
    assert.doesNotMatch(source, /DashboardBanner|dashboard_banner|banner_agent/);
    assert.doesNotMatch(source, /\bbanner_(items|generated_at|source|enabled|cron|max_items|last_run|next_run)/);
  }
  assert.match(autoRefresh, /createAutoRefreshPoller/);
  assert.match(autoRefresh, /latest_activity/);
});
