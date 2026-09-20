"use client";

import { useMemo, useState } from "react";
import { Download, Eye, LoaderCircle } from "lucide-react";
import BackupDependencySummary from "@/components/BackupDependencySummary";
import BackupPresetSelector from "@/components/BackupPresetSelector";
import BackupSectionChecklist from "@/components/BackupSectionChecklist";
import type {
  BackupExportOptions,
  BackupExportPreview,
  BackupExportSelection,
  BackupPreset,
} from "@/types/backup";

const DEFAULT_PRESET = "recommended_full_user_backup";
const inputClass = "w-full rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm";

function splitList(value: string) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function getFilename(disposition: string | null) {
  const encoded = disposition?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plain = disposition?.match(/filename="?([^";]+)"?/i)?.[1];
  return decodeURIComponent(encoded ?? plain ?? `option-income-lab-${new Date().toISOString()}.oil-backup.zip`);
}

export default function BackupExportView({
  options,
  optionsError,
}: {
  options: BackupExportOptions;
  optionsError?: string | null;
}) {
  const defaultSections = options.sections.map((section) =>
    typeof section === "string" ? section : section.id ?? section.name,
  );
  const [preset, setPreset] = useState<BackupPreset>(DEFAULT_PRESET);
  const [sections, setSections] = useState(defaultSections);
  const [symbols, setSymbols] = useState("");
  const [accounts, setAccounts] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [includePaper, setIncludePaper] = useState(true);
  const [includeSourceRow, setIncludeSourceRow] = useState(true);
  const [preview, setPreview] = useState<BackupExportPreview | null>(null);
  const [previewKey, setPreviewKey] = useState("");
  const [busy, setBusy] = useState<"preview" | "download" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selection = useMemo<BackupExportSelection>(() => {
    const custom = preset === "custom";
    return {
      preset,
      ...(custom ? { sections } : {}),
      ...(custom
        ? {
            filters: {
              symbols: splitList(symbols),
              account_ids: splitList(accounts),
              date_from: dateFrom || undefined,
              date_to: dateTo || undefined,
            },
            include_paper_positions: includePaper,
            include_source_row: includeSourceRow,
          }
        : {}),
    };
  }, [accounts, dateFrom, dateTo, includePaper, includeSourceRow, preset, sections, symbols]);
  const selectionKey = JSON.stringify(selection);
  const previewIsCurrent = Boolean(preview?.selection_fingerprint && previewKey === selectionKey);

  function changePreset(value: BackupPreset) {
    setPreset(value);
    setPreview(null);
  }

  async function requestPreview() {
    setBusy("preview");
    setError(null);
    try {
      const response = await fetch("/api/backups/export/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(selection),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? data.error ?? "Preview failed");
      setPreview(data);
      setPreviewKey(selectionKey);
    } catch (caught) {
      setPreview(null);
      setError(caught instanceof Error ? caught.message : "Preview failed");
    } finally {
      setBusy(null);
    }
  }

  async function download() {
    if (!previewIsCurrent || !preview) return;
    setBusy("download");
    setError(null);
    try {
      const response = await fetch("/api/backups/export/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...selection, preview_fingerprint: preview.selection_fingerprint }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail ?? data.error ?? "Export failed");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = getFilename(response.headers.get("content-disposition"));
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Export failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <section className="surface space-y-5 p-5">
        {optionsError ? (
          <p role="alert" className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 p-3 text-sm text-accent-red">
            ⚠️ {optionsError}
          </p>
        ) : null}
        <BackupPresetSelector presets={options.presets} value={preset} onChange={changePreset} disabled={busy !== null} />
        {preset === "custom" ? (
          <div className="space-y-5 border-t border-border pt-5">
            <BackupSectionChecklist sections={options.sections} selected={sections} onChange={setSections} disabled={busy !== null} />
            <div>
              <h3 className="mb-3 text-sm font-semibold">Optional filters</h3>
              <div className="grid gap-3 md:grid-cols-2">
                <label className="text-sm">Symbols <span className="text-text-muted">(comma-separated)</span><input value={symbols} onChange={(e) => setSymbols(e.target.value)} className={inputClass} /></label>
                <label className="text-sm">Account IDs <span className="text-text-muted">(comma-separated)</span><input value={accounts} onChange={(e) => setAccounts(e.target.value)} className={inputClass} /></label>
                <label className="text-sm">From date<input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className={inputClass} /></label>
                <label className="text-sm">To date<input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className={inputClass} /></label>
              </div>
            </div>
            <div className="flex flex-col gap-2 text-sm">
              <label><input type="checkbox" checked={includePaper} onChange={(e) => setIncludePaper(e.target.checked)} className="mr-2 accent-[var(--color-accent-blue)]" />Include paper option positions</label>
              <label><input type="checkbox" checked={includeSourceRow} onChange={(e) => setIncludeSourceRow(e.target.checked)} className="mr-2 accent-[var(--color-accent-blue)]" />Include original broker source rows</label>
            </div>
          </div>
        ) : null}
        <div className="rounded-[var(--radius)] border border-accent-orange/40 bg-accent-orange/10 p-3 text-sm">
          Backups contain sensitive portfolio and financial information. Store downloaded files securely.
        </div>
        <div className="flex flex-wrap gap-3">
          <button type="button" onClick={requestPreview} disabled={busy !== null || (preset === "custom" && sections.length === 0)} className="inline-flex items-center gap-2 rounded-full border border-accent-blue px-4 py-2 text-sm font-semibold text-accent-blue disabled:opacity-50">
            {busy === "preview" ? <LoaderCircle size={16} className="animate-spin" /> : <Eye size={16} />} Preview
          </button>
          <button type="button" onClick={download} disabled={!previewIsCurrent || busy !== null} className="inline-flex items-center gap-2 rounded-full bg-accent-blue px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">
            {busy === "download" ? <LoaderCircle size={16} className="animate-spin" /> : <Download size={16} />} Download current preview
          </button>
        </div>
        {error ? <p role="alert" className="text-sm text-accent-red">⚠️ {error}</p> : null}
        {preview ? (
          <BackupDependencySummary
            preview={preview}
            includesSourceRows={preset === "custom" ? includeSourceRow : preset === DEFAULT_PRESET}
          />
        ) : null}
      </section>
    </div>
  );
}
