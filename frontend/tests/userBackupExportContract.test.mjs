import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = (path) => readFileSync(join(root, path), "utf8");

const exportView = source("src/components/BackupExportView.tsx");
const dependencySummary = source("src/components/BackupDependencySummary.tsx");
const proxy = source("src/lib/backupProxy.ts");
const nav = source("src/components/TopNav.tsx");
const exportPage = source("src/app/settings/export/page.tsx");
const backupTypes = source("src/types/backup.ts");

describe("user backup export contract", () => {
  it("defaults to the recommended preset and offers preview-gated download", () => {
    assert.match(exportView, /recommended_full_user_backup/);
    assert.match(exportView, /selection_fingerprint/);
    assert.match(exportView, /preview_fingerprint/);
    assert.match(exportView, /previewIsCurrent/);
    assert.match(exportView, /disabled=\{!previewIsCurrent/);
  });

  it("supports custom sections, filters, paper positions, and source rows", () => {
    for (const token of [
      "BackupSectionChecklist",
      "account_ids",
      "date_from",
      "date_to",
      "include_paper_positions",
      "include_source_row",
    ]) assert.ok(exportView.includes(token), `Missing ${token}`);
  });

  it("renders effective dependency scope, counts, exclusions, and warnings", () => {
    for (const token of [
      "requested_sections",
      "effective_sections",
      "added_dependencies",
      "counts",
      "warnings",
      "exclusions",
      "sensitive financial information",
    ]) assert.ok(dependencySummary.includes(token), `Missing ${token}`);
  });

  it("preserves binary response and required export headers", () => {
    for (const header of [
      "content-type",
      "content-disposition",
      "x-backup-export-id",
      "x-backup-content-sha256",
      "x-backup-archive-sha256",
    ]) assert.ok(proxy.includes(header), `Proxy must preserve ${header}`);
    assert.match(proxy, /arrayBuffer\(\)/);
  });

  it("adds backup links without changing the portfolio CSV import route", () => {
    assert.match(nav, /href: "\/settings\/export"/);
    assert.match(nav, /href: "\/settings\/import"/);
    assert.doesNotMatch(nav, /href: "\/portfolio\/import"/);
  });

  it("does not expose automatic Job configuration or status in the application", () => {
    assert.equal(existsSync(join(root, "src/components/AutomaticBackupCard.tsx")), false);
    assert.equal(existsSync(join(root, "src/lib/backupStatus.ts")), false);
    assert.equal(existsSync(join(root, "src/app/api/backups/automatic/route.ts")), false);
    assert.doesNotMatch(exportPage, /backups\/automatic|BackupAutomaticStatus/);
    assert.doesNotMatch(exportView, /AutomaticBackup|automaticStatus|automaticError/);
    assert.doesNotMatch(backupTypes, /BackupAutomatic/);
  });
});
