"use client";

import { useState, useRef } from "react";
import { FileUp, Upload } from "lucide-react";
import {
  createImportSession,
  generatePreview,
  commitImport,
} from "@/lib/portfolio-api";
import type {
  ImportSession,
  ImportFormat,
  ImportPreviewResponse,
  CommitResult,
} from "@/types/import";
import ImportQuestionCard from "./ImportQuestionCard";
import ImportPreview from "./ImportPreview";

type Phase = "upload" | "questions" | "preview" | "committed";

const FORMAT_OPTIONS: Array<{ value: "" | ImportFormat; label: string }> = [
  { value: "", label: "Auto-detect" },
  { value: "dividends", label: "Dividends" },
  { value: "purchases", label: "Purchases" },
  { value: "sales", label: "Sales" },
];

/**
 * Conversational import UI.
 *
 * Flow: upload → answer batch+entity questions → preview → explicit confirm → committed.
 * No mock data — all errors surface to the user.
 */
export default function ImportChat() {
  const [phase, setPhase] = useState<Phase>("upload");
  const [session, setSession] = useState<ImportSession | null>(null);
  const [preview, setPreview] = useState<ImportPreviewResponse | null>(null);
  const [committed, setCommitted] = useState<CommitResult | null>(null);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [committing, setCommitting] = useState(false);
  const [generatingPreview, setGeneratingPreview] = useState(false);

  // Upload form state
  const [file, setFile] = useState<File | null>(null);
  const [pasteContent, setPasteContent] = useState("");
  const [inputMode, setInputMode] = useState<"file" | "paste">("file");
  const [format, setFormat] = useState<"" | ImportFormat>("");
  const [currency, setCurrency] = useState("EUR");
  const [accountId, setAccountId] = useState("");
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) { setFile(f); setInputMode("file"); }
  }

  async function handleUpload(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const params = {
        ...(inputMode === "file" && file ? { file } : { content: pasteContent }),
        format_hint: format || undefined,
        currency: currency || "EUR",
        account_id: accountId.trim() || undefined,
      };
      const s = await createImportSession(params);
      setSession(s);
      setPhase("questions");
    } catch (err) {
      const e = err as { data?: { detail?: string; error?: string } };
      setError(e.data?.detail ?? e.data?.error ?? (err instanceof Error ? err.message : "Upload failed"));
    } finally {
      setLoading(false);
    }
  }

  function handleQuestionAnswered(updated: ImportSession) {
    setSession(updated);
  }

  async function handleRequestPreview() {
    if (!session) return;
    setError(null);
    setGeneratingPreview(true);
    try {
      const p = await generatePreview(session.session_id);
      setPreview(p);
      setSession((s) => s ? { ...s, state: p.state } : s);
      setPhase("preview");
    } catch (err) {
      const e = err as { status?: number; data?: { error?: string; detail?: string; pending?: unknown[] } };
      if (e.status === 409 && e.data?.error === "unresolved_questions") {
        setError("Please answer all questions before previewing.");
      } else {
        setError(e.data?.detail ?? e.data?.error ?? (err instanceof Error ? err.message : "Preview failed"));
      }
    } finally {
      setGeneratingPreview(false);
    }
  }

  async function handleCommit() {
    if (!session) return;
    setError(null);
    setCommitting(true);
    try {
      const result = await commitImport(session.session_id);
      setCommitted(result);
      setPhase("committed");
    } catch (err) {
      const e = err as { data?: { error?: string; detail?: string } };
      setError(e.data?.detail ?? e.data?.error ?? (err instanceof Error ? err.message : "Commit failed"));
    } finally {
      setCommitting(false);
    }
  }

  function reset() {
    setPhase("upload");
    setSession(null);
    setPreview(null);
    setCommitted(null);
    setFile(null);
    setPasteContent("");
    setFormat("");
    setCurrency("EUR");
    setAccountId("");
    setError(null);
  }

  const inputCls =
    "rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none";

  // ─── COMMITTED ────────────────────────────────────────────────────────────

  if (phase === "committed" && committed) {
    return (
      <div className="rounded-[var(--radius-card)] border border-accent-green/30 bg-accent-green/5 p-8 text-center space-y-4 animate-fade-up">
        <div className="text-4xl">✓</div>
        <div className="text-xl font-semibold text-text">Import committed</div>
        <div className="text-sm text-text-muted">
          <span className="text-text font-semibold">{committed.committed_count}</span> movements
          committed
          {committed.skipped_count > 0 && (
            <>
              {" · "}
              <span className="text-accent-orange">{committed.skipped_count}</span> skipped
            </>
          )}
        </div>
        <div className="flex justify-center gap-3 pt-2">
          <a
            href="/portfolio/holdings"
            className="rounded-[var(--radius)] border border-border px-4 py-2 text-sm text-text-muted hover:bg-bg-hover"
          >
            View Holdings
          </a>
          <button
            type="button"
            onClick={reset}
            className="rounded-[var(--radius)] bg-[image:var(--grad-blue)] px-4 py-2 text-sm font-medium text-white hover:opacity-90"
          >
            Import another file
          </button>
        </div>
      </div>
    );
  }

  // ─── PREVIEW ─────────────────────────────────────────────────────────────

  if (phase === "preview" && preview) {
    return (
      <div className="space-y-4">
        {error && (
          <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm text-accent-red">
            {error}
          </div>
        )}
        <ImportPreview
          preview={preview.preview}
          onCommit={handleCommit}
          onBack={() => setPhase("questions")}
          committing={committing}
        />
      </div>
    );
  }

  // ─── QUESTIONS ────────────────────────────────────────────────────────────

  if (phase === "questions" && session) {
    const questions = session.questions ?? [];
    const unanswered = questions.filter((q) => q.answer === null);
    const allAnswered = unanswered.length === 0;

    const batchQs = questions.filter((q) => q.scope === "BATCH");
    const entityQs = questions.filter((q) => q.scope === "ENTITY");

    return (
      <div className="space-y-6">
        {/* Session info bar */}
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <span className="rounded-full bg-bg-card border border-border px-3 py-1">
            <span className="text-text-muted">Format: </span>
            <span className="font-medium text-text capitalize">
              {session.detected_format ?? "unknown"}
            </span>
          </span>
          {session.staged_summary?.total_rows !== undefined && (
            <span className="text-text-muted">
              {session.staged_summary.total_rows} rows
              {session.staged_summary.date_range && (
                <> · {session.staged_summary.date_range[0]} – {session.staged_summary.date_range[1]}</>
              )}
            </span>
          )}
          <span
            className={`rounded-full px-3 py-1 text-xs ${
              allAnswered
                ? "bg-accent-green/15 text-accent-green"
                : "bg-accent-orange/15 text-accent-orange"
            }`}
          >
            {allAnswered ? "All answered" : `${unanswered.length} pending`}
          </span>
        </div>

        {/* Upload-level warnings (e.g. NEGATIVE_INVENTORY from parse) */}
        {session.warnings && session.warnings.length > 0 && (
          <div className="rounded-[var(--radius)] border border-accent-orange/30 bg-accent-orange/5 p-4 space-y-1">
            <div className="text-xs font-semibold uppercase tracking-wide text-accent-orange mb-2">
              ⚠ Persistent warnings
            </div>
            {session.warnings.map((w, i) => (
              <div key={i} className="text-sm text-text-muted">
                <span className="font-medium text-text mr-1">
                  {warningLabel(w.type)}:
                </span>
                {w.message}
              </div>
            ))}
          </div>
        )}

        {error && (
          <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm text-accent-red">
            {error}
          </div>
        )}

        {/* Batch questions */}
        {batchQs.length > 0 && (
          <section className="space-y-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-text-muted">
              Batch settings
            </div>
            {batchQs.map((q) => (
              <ImportQuestionCard
                key={q.question_id}
                question={q}
                sessionId={session.session_id}
                onAnswered={handleQuestionAnswered}
              />
            ))}
          </section>
        )}

        {/* Entity questions */}
        {entityQs.length > 0 && (
          <section className="space-y-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-text-muted">
              Security mapping — {entityQs.filter((q) => q.answer === null).length} of{" "}
              {entityQs.length} remaining
            </div>
            {entityQs.map((q) => (
              <ImportQuestionCard
                key={q.question_id}
                question={q}
                sessionId={session.session_id}
                onAnswered={handleQuestionAnswered}
              />
            ))}
          </section>
        )}

        {/* Preview button */}
        <div className="flex gap-3 pt-2">
          <button
            type="button"
            onClick={handleRequestPreview}
            disabled={!allAnswered || generatingPreview}
            className="rounded-[var(--radius)] bg-[image:var(--grad-blue)] px-6 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-40 shadow-[var(--shadow-glow-blue)]"
          >
            {generatingPreview ? "Generating preview…" : "Preview movements →"}
          </button>
          <button
            type="button"
            onClick={reset}
            className="rounded-[var(--radius)] border border-border px-4 py-2 text-sm text-text-muted hover:bg-bg-hover"
          >
            Start over
          </button>
        </div>
      </div>
    );
  }

  // ─── UPLOAD ───────────────────────────────────────────────────────────────

  return (
    <form onSubmit={handleUpload} className="space-y-6">
      {error && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm text-accent-red">
          {error}
        </div>
      )}

      {/* Mode toggle */}
      <div className="flex gap-2">
        {(["file", "paste"] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setInputMode(m)}
            className={`rounded-[var(--radius-pill)] px-4 py-1.5 text-sm transition-colors ${
              inputMode === m
                ? "bg-bg-hover text-text font-medium"
                : "text-text-muted hover:bg-bg-hover hover:text-text"
            }`}
          >
            {m === "file" ? "Upload file" : "Paste content"}
          </button>
        ))}
      </div>

      {inputMode === "file" ? (
        // Drag-and-drop zone
        <div
          onDrop={handleDrop}
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onClick={() => fileRef.current?.click()}
          className={`flex flex-col items-center justify-center gap-3 cursor-pointer rounded-[var(--radius-card)] border-2 border-dashed px-8 py-12 transition-colors ${
            dragging
              ? "border-accent-blue bg-accent-blue/5"
              : "border-border hover:border-border bg-bg-card/50 hover:bg-bg-hover/20"
          }`}
        >
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.tsv,.txt"
            className="hidden"
            onChange={(e) => { setFile(e.target.files?.[0] ?? null); }}
          />
          {file ? (
            <>
              <FileUp size={32} className="text-accent-blue" />
              <div className="text-sm font-medium text-text">{file.name}</div>
              <div className="text-xs text-text-muted">
                {(file.size / 1024).toFixed(1)} KB · Click to change
              </div>
            </>
          ) : (
            <>
              <Upload size={32} className="text-text-muted" />
              <div className="text-sm text-text-muted">
                Drag & drop a CSV file or <span className="text-accent-blue">browse</span>
              </div>
              <div className="text-xs text-text-muted">
                Accepts dividends, purchases, or sales CSV
              </div>
            </>
          )}
        </div>
      ) : (
        // Paste textarea
        <textarea
          value={pasteContent}
          onChange={(e) => setPasteContent(e.target.value)}
          placeholder={"Paste CSV rows here…\nAño\tEmpresa\tFecha de cobro\t…"}
          rows={8}
          className="w-full rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 font-mono text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none resize-y"
        />
      )}

      {/* Options row */}
      <div className="grid grid-cols-3 gap-4">
        <div>
          <label className="block text-xs font-medium text-text-muted mb-1">
            Format
          </label>
          <select
            value={format}
            onChange={(e) => setFormat(e.target.value as "" | ImportFormat)}
            className={`${inputCls} w-full`}
          >
            {FORMAT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-text-muted mb-1">
            Batch currency
          </label>
          <input
            value={currency}
            onChange={(e) => setCurrency(e.target.value.toUpperCase())}
            maxLength={3}
            placeholder="EUR"
            className={`${inputCls} w-full`}
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-text-muted mb-1">
            Broker / account <span className="text-text-muted font-normal">(optional)</span>
          </label>
          <input
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
            placeholder="Leave blank to skip"
            className={`${inputCls} w-full`}
          />
        </div>
      </div>

      <button
        type="submit"
        disabled={loading || (inputMode === "file" ? !file : !pasteContent.trim())}
        className="rounded-[var(--radius)] bg-[image:var(--grad-blue)] px-6 py-2.5 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-40 shadow-[var(--shadow-glow-blue)]"
      >
        {loading ? "Uploading…" : "Upload & start import →"}
      </button>
    </form>
  );
}

function warningLabel(type: string): string {
  const labels: Record<string, string> = {
    NEGATIVE_INVENTORY: "Negative inventory",
    RIGHTS_AMOUNT: "Rights amount",
    PROBABLE_DUPLICATE: "Probable duplicate",
  };
  return labels[type] ?? type;
}
