"use client";

import { useState } from "react";
import { CheckCircle2, FileArchive, LoaderCircle, ShieldCheck, Upload } from "lucide-react";
import BackupDryRunReport, { dryRunHasBlockingIssues } from "@/components/BackupDryRunReport";
import type { BackupDryRunPlan, BackupImportResult, BackupValidationReport } from "@/types/backup";

type BusyStep = "validate" | "dry-run" | "apply" | null;

function asText(value: unknown) {
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function postFile(path: string, file: File, fields: Record<string, string> = {}) {
  const body = new FormData();
  body.append("file", file);
  for (const [name, value] of Object.entries(fields)) body.append(name, value);
  return fetch(path, { method: "POST", body });
}

export default function BackupImportView() {
  const [file, setFile] = useState<File | null>(null);
  const [validation, setValidation] = useState<BackupValidationReport | null>(null);
  const [dryRun, setDryRun] = useState<BackupDryRunPlan | null>(null);
  const [result, setResult] = useState<BackupImportResult | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState<BusyStep>(null);
  const [error, setError] = useState<string | null>(null);

  const validationInvalid =
    !validation?.valid ||
    validation.compatible === false ||
    validation.tampered === true ||
    validation.future_schema === true ||
    Boolean(validation.errors?.length) ||
    Boolean(validation.dependency_errors?.length) ||
    validation.checksums_valid === false;
  const validationBlocked =
    validationInvalid || Boolean(validation?.collisions?.length);
  const canApply =
    Boolean(file && validation && dryRun?.dry_run_fingerprint && confirmed) &&
    !validationBlocked &&
    !dryRunHasBlockingIssues(dryRun!) &&
    busy === null &&
    !result;

  function selectFile(next: File | null) {
    setValidation(null);
    setDryRun(null);
    setResult(null);
    setConfirmed(false);
    setError(null);
    if (next && !next.name.toLowerCase().endsWith(".oil-backup.zip")) {
      setFile(null);
      setError("Choose an .oil-backup.zip file.");
      return;
    }
    setFile(next);
  }

  async function validate() {
    if (!file || busy) return;
    setBusy("validate");
    setError(null);
    setValidation(null);
    setDryRun(null);
    setResult(null);
    try {
      const response = await postFile("/api/backups/import/validate", file);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? data.error ?? "Validation failed");
      setValidation(data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Validation failed");
    } finally {
      setBusy(null);
    }
  }

  async function runDryRun() {
    if (!file || validationInvalid || busy) return;
    setBusy("dry-run");
    setError(null);
    setDryRun(null);
    setConfirmed(false);
    try {
      const response = await postFile("/api/backups/import/dry-run", file, { mode: "create_only" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? data.error ?? "Dry-run failed");
      setDryRun(data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Dry-run failed");
    } finally {
      setBusy(null);
    }
  }

  async function apply() {
    if (!file || !dryRun?.dry_run_fingerprint || !canApply) return;
    setBusy("apply");
    setError(null);
    try {
      const response = await postFile("/api/backups/import/apply", file, {
        mode: "create_only",
        dry_run_fingerprint: dryRun.dry_run_fingerprint,
        confirm: "true",
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? data.error ?? "Restore failed");
      setResult(data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Restore failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <section className="surface p-5">
        <h2 className="text-lg font-semibold">1. Choose and validate archive</h2>
        <p className="mt-1 text-sm text-text-muted">Validation checks format, schema, checksums, dependencies, and destination collisions without writing user data.</p>
        <label className="mt-4 flex cursor-pointer items-center gap-3 rounded-[var(--radius)] border border-dashed border-border bg-bg-input p-4">
          <FileArchive className="text-accent-blue" />
          <span className="min-w-0 flex-1 truncate text-sm">{file?.name ?? "Choose .oil-backup.zip"}</span>
          <input type="file" accept=".oil-backup.zip,application/zip" disabled={busy !== null} onChange={(e) => selectFile(e.target.files?.[0] ?? null)} className="sr-only" />
        </label>
        <button type="button" onClick={validate} disabled={!file || busy !== null} className="mt-4 inline-flex items-center gap-2 rounded-full bg-accent-blue px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">
          {busy === "validate" ? <LoaderCircle size={16} className="animate-spin" /> : <ShieldCheck size={16} />} Validate without writes
        </button>
        {validation ? (
          <div className={`mt-4 rounded-[var(--radius)] border p-4 text-sm ${validationBlocked ? "border-accent-red/40 bg-accent-red/10" : "border-accent-green/40 bg-accent-green/10"}`}>
            <p className="font-semibold">{validationInvalid ? "Archive is invalid" : validationBlocked ? "Archive is valid, but destination conflicts block restore" : "Archive is valid and compatible"}</p>
            <dl className="mt-2 grid gap-2 sm:grid-cols-2">
              <div><dt className="text-text-muted">Compatibility</dt><dd>{validation.compatible === false ? "Incompatible" : asText(validation.compatibility ?? "Compatible")}</dd></div>
              <div><dt className="text-text-muted">Checksums</dt><dd>{validation.checksums_valid === false ? "Invalid" : "Verified"}</dd></div>
              <div><dt className="text-text-muted">Dependency errors</dt><dd>{validation.dependency_errors?.length ?? 0}</dd></div>
              <div><dt className="text-text-muted">Collisions</dt><dd>{validation.collisions?.length ?? 0}</dd></div>
            </dl>
            {validation.section_counts ? <p className="mt-2 text-text-muted">Sections: {Object.entries(validation.section_counts).map(([key, value]) => `${key} ${value}`).join(" · ")}</p> : null}
            {validation.warnings?.length ? <ul className="mt-2">{validation.warnings.map((warning) => <li key={warning}>⚠️ {warning}</li>)}</ul> : null}
          </div>
        ) : null}
      </section>

      <section className="surface p-5">
        <h2 className="text-lg font-semibold">2. Dry-run against this destination</h2>
        <p className="mt-1 text-sm text-text-muted">Mode is fixed to Create missing / skip identical. Existing different records are never overwritten.</p>
        <button type="button" onClick={runDryRun} disabled={!validation || validationInvalid || busy !== null} className="mt-4 inline-flex items-center gap-2 rounded-full border border-accent-blue px-4 py-2 text-sm font-semibold text-accent-blue disabled:opacity-40">
          {busy === "dry-run" ? <LoaderCircle size={16} className="animate-spin" /> : <CheckCircle2 size={16} />} Run dry-run
        </button>
        {dryRun ? <div className="mt-4"><BackupDryRunReport report={dryRun} /></div> : null}
      </section>

      <section className="surface p-5">
        <h2 className="text-lg font-semibold">3. Confirm and restore</h2>
        <p className="mt-1 text-sm text-text-muted">The server re-uploads and revalidates the archive, then recomputes the plan before any create.</p>
        <label className="mt-4 flex items-start gap-3 text-sm">
          <input type="checkbox" checked={confirmed} disabled={!dryRun || dryRunHasBlockingIssues(dryRun) || Boolean(result) || busy !== null} onChange={(e) => setConfirmed(e.target.checked)} className="mt-1 accent-[var(--color-accent-blue)]" />
          <span>I understand this create-only restore will create missing records and skip identical records. It will not update or delete existing data.</span>
        </label>
        <button type="button" onClick={apply} disabled={!canApply} className="mt-4 inline-flex items-center gap-2 rounded-full bg-accent-green px-4 py-2 text-sm font-semibold text-black disabled:opacity-40">
          {busy === "apply" ? <LoaderCircle size={16} className="animate-spin" /> : <Upload size={16} />} Apply create-only restore
        </button>
        {busy === "apply" ? <p role="status" className="mt-3 text-sm text-text-muted">Restore in progress. Keep this page open…</p> : null}
        {result ? (
          <div className={`mt-4 rounded-[var(--radius)] border p-4 ${result.status === "COMPLETED" ? "border-accent-green/40 bg-accent-green/10" : "border-accent-orange/40 bg-accent-orange/10"}`}>
            <h3 className="font-semibold">Restore result: {result.status}</h3>
            <p className="mt-1 text-sm">{result.human_summary ?? result.summary ?? "The restore run finished."}</p>
            {result.import_run_id ? <p className="mt-2 font-mono text-xs text-text-muted">Run ID: {result.import_run_id}</p> : null}
            {result.counts || result.created || result.skipped ? (
              <p className="mt-2 text-sm text-text-muted">
                {[
                  ...Object.entries(result.counts ?? {}),
                  ...Object.entries(result.created ?? {}).map(([key, value]) => [`created ${key}`, value] as const),
                  ...Object.entries(result.skipped ?? {}).map(([key, value]) => [`skipped ${key}`, value] as const),
                ].map(([key, value]) => `${key}: ${value}`).join(" · ")}
              </p>
            ) : null}
            {result.errors?.map((item) => <p key={item} className="mt-1 text-sm text-accent-red">{item}</p>)}
          </div>
        ) : null}
        {error ? <p role="alert" className="mt-4 text-sm text-accent-red">⚠️ {error}</p> : null}
      </section>
    </div>
  );
}
