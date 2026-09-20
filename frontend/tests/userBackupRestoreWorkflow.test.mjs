import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = (path) => readFileSync(join(root, path), "utf8");
const importView = source("src/components/BackupImportView.tsx");
const report = source("src/components/BackupDryRunReport.tsx");
const proxy = source("src/lib/backupProxy.ts");

describe("user backup restore workflow", () => {
  it("accepts only the backup archive suffix and resets stale workflow state", () => {
    assert.match(importView, /\.oil-backup\.zip/);
    assert.match(importView, /setValidation\(null\)/);
    assert.match(importView, /setDryRun\(null\)/);
    assert.match(importView, /setResult\(null\)/);
  });

  it("keeps validation, dry-run, and apply as distinct multipart requests", () => {
    for (const endpoint of [
      "/api/backups/import/validate",
      "/api/backups/import/dry-run",
      "/api/backups/import/apply",
    ]) assert.ok(importView.includes(endpoint), `Missing ${endpoint}`);
    assert.match(importView, /new FormData\(\)/);
    assert.match(proxy, /request\.headers\.get\("content-type"\)/);
  });

  it("exposes only create-only mode with explicit confirmation and fingerprint", () => {
    assert.match(importView, /mode: "create_only"/);
    assert.match(importView, /dry_run_fingerprint/);
    assert.match(importView, /confirm: "true"/);
    assert.match(importView, /I understand this create-only restore/);
    assert.doesNotMatch(importView, /update_existing|replace_all|delete_existing/);
  });

  it("blocks conflicts, missing references, invariants, and invalid validation", () => {
    for (const status of [
      "CONFLICT_REQUIRES_CHOICE",
      "BLOCKED_MISSING_REFERENCE",
      "BLOCKED_INVARIANT",
    ]) assert.ok(report.includes(status), `Missing ${status} guard`);
    for (const guard of ["tampered", "future_schema", "checksums_valid", "dependency_errors", "collisions"]) {
      assert.ok(importView.includes(guard), `Missing ${guard} guard`);
    }
  });

  it("prevents double submit and renders a terminal result", () => {
    assert.match(importView, /busy === null/);
    assert.match(importView, /disabled=\{!canApply\}/);
    assert.match(importView, /Restore result:/);
    assert.match(importView, /PARTIAL_REQUIRES_ATTENTION|result\.status/);
  });
});
