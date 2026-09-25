import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("../src/app/dashboard/page.tsx", import.meta.url), "utf8");
const component = readFileSync(
  new URL("../src/components/DashboardBanner.tsx", import.meta.url),
  "utf8",
);
const types = readFileSync(new URL("../src/types/dashboard.ts", import.meta.url), "utf8");

test("dashboard exposes and renders banner source metadata", () => {
  assert.match(types, /banner_source_as_of\?: string \| null/);
  assert.match(types, /banner_source_watermarks\?: Record<string, string>/);
  assert.match(types, /banner_source_counts\?: Record<string, number>/);
  assert.match(page, /sourceAsOf=\{d\.banner_source_as_of\}/);
  assert.match(page, /sourceWatermarks=\{d\.banner_source_watermarks\}/);
  assert.match(page, /sourceCounts=\{d\.banner_source_counts\}/);
  assert.match(component, /Data as of/);
  assert.match(component, /market snapshot/);
  assert.match(component, /recent activit/);
});
