"use client";

import { useState, useCallback } from "react";
import type { SecurityMasterInfo, Enrichment, WatchlistToggles } from "@/types/symbol-detail";

// ─── helpers ─────────────────────────────────────────────────────────────────

function timeAgoShort(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  const sec = Math.floor((Date.now() - d.getTime()) / 1000);
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

function ro(value: string | null | undefined, placeholder = "—"): string {
  return value?.trim() || placeholder;
}

// ─── sub-components ───────────────────────────────────────────────────────────

function FieldLabel({ htmlFor, children }: { htmlFor?: string; children: React.ReactNode }) {
  return (
    <label
      htmlFor={htmlFor}
      className="block text-xs font-medium text-text-muted mb-0.5"
    >
      {children}
    </label>
  );
}

function ReadOnlyField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs font-medium text-text-muted mb-0.5">{label}</div>
      <div className="font-mono text-sm text-text bg-bg-input rounded-[var(--radius)] border border-border px-3 py-1.5 select-all">
        {value}
      </div>
    </div>
  );
}

function TextInput({
  id, value, onChange, placeholder, disabled, monospace,
}: {
  id: string; value: string; onChange: (v: string) => void;
  placeholder?: string; disabled?: boolean; monospace?: boolean;
}) {
  return (
    <input
      id={id}
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      disabled={disabled}
      className={`w-full rounded-[var(--radius)] border border-border bg-bg-input px-3 py-1.5 text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none disabled:opacity-50 ${monospace ? "font-mono" : ""}`}
    />
  );
}

function ToggleRow({
  id, label, checked, onChange, disabled, disabledReason,
}: {
  id: string; label: string; checked: boolean;
  onChange: (v: boolean) => void; disabled?: boolean; disabledReason?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5">
      <div>
        <label
          htmlFor={id}
          className={`text-sm font-medium ${disabled ? "text-text-muted" : "text-text"} cursor-pointer`}
        >
          {label}
        </label>
        {disabled && disabledReason && (
          <p className="text-xs text-text-muted mt-0.5">{disabledReason}</p>
        )}
      </div>
      <button
        id={id}
        role="switch"
        type="button"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => !disabled && onChange(!checked)}
        className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/60 disabled:cursor-not-allowed disabled:opacity-40 ${checked ? "bg-accent-blue" : "bg-border"}`}
      >
        <span
          aria-hidden
          className={`pointer-events-none inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform ${checked ? "translate-x-4" : "translate-x-0.5"}`}
        />
      </button>
    </div>
  );
}

// ─── main component ───────────────────────────────────────────────────────────

interface Props {
  symbol: string;
  security: SecurityMasterInfo;
  enrichment: Enrichment;
  watchlist: WatchlistToggles;
  telegramEnabled: boolean;
  usOptionsEligible: boolean;
}

interface FormState {
  company_name: string;
  isin: string;
  cusip: string;
  sedol: string;
  listing_currency: string;
  country: string;
  asset_class: string;
  yfinance: string;
  tradingview: string;
}

function initForm(sec: SecurityMasterInfo): FormState {
  return {
    company_name: sec.company_name ?? "",
    isin: sec.isin ?? "",
    cusip: sec.cusip ?? "",
    sedol: sec.sedol ?? "",
    listing_currency: sec.listing_currency ?? "",
    country: sec.country ?? "",
    asset_class: sec.asset_class ?? "",
    yfinance: sec.provider_symbols?.yfinance ?? "",
    tradingview: sec.provider_symbols?.tradingview ?? "",
  };
}

export default function SymbolConfigurationCard({
  symbol,
  security: initialSecurity,
  enrichment: initialEnrichment,
  watchlist: initialWatchlist,
  telegramEnabled: initialTelegram,
  usOptionsEligible,
}: Props) {
  // Security / form state
  const [security, setSecurity] = useState(initialSecurity);
  const [form, setForm] = useState<FormState>(() => initForm(initialSecurity));
  const [dirty, setDirty] = useState(false);

  // Save state
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveConflict, setSaveConflict] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Enrichment state
  const [enrichment, setEnrichment] = useState(initialEnrichment);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshStatus, setRefreshStatus] = useState<{ ok: boolean; detail?: string } | null>(null);

  // Toggle state
  const [toggles, setToggles] = useState({
    covered_call: initialWatchlist.covered_call,
    cash_secured_put: initialWatchlist.cash_secured_put,
    buy_tracker: initialWatchlist.buy_tracker,
    telegram: initialTelegram,
  });
  const [toggling, setToggling] = useState<string | null>(null);
  const [toggleError, setToggleError] = useState<string | null>(null);

  function patch<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }));
    setDirty(true);
    setSaveError(null);
    setSaveConflict(false);
    setSaveSuccess(false);
  }

  const save = useCallback(async () => {
    setSaving(true);
    setSaveError(null);
    setSaveConflict(false);
    setSaveSuccess(false);
    try {
      const body: Record<string, unknown> = {
        _etag: security._etag,
        company_name: form.company_name || undefined,
        isin: form.isin || null,
        cusip: form.cusip || null,
        sedol: form.sedol || null,
        listing_currency: form.listing_currency || undefined,
        country: form.country || null,
        asset_class: form.asset_class || undefined,
      };

      // Build provider_symbols only when at least one override is set
      const providerSymbols: Record<string, string> = {};
      if (form.yfinance.trim()) providerSymbols.yfinance = form.yfinance.trim();
      if (form.tradingview.trim()) providerSymbols.tradingview = form.tradingview.trim();
      if (Object.keys(providerSymbols).length > 0 || security.provider_symbols) {
        body.provider_symbols = Object.keys(providerSymbols).length > 0 ? providerSymbols : null;
      }

      const res = await fetch(`/api/symbols/${encodeURIComponent(symbol)}/security`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({})) as Record<string, unknown>;

      if (res.ok) {
        // Backend returns updated security projection — refresh local state
        const updated = (data.security ?? data) as SecurityMasterInfo;
        setSecurity(updated);
        setForm(initForm(updated));
        setDirty(false);
        setSaveSuccess(true);
        setTimeout(() => setSaveSuccess(false), 3000);
      } else if (res.status === 409) {
        if ((data.error as string) === "collision") {
          setSaveError(
            `Identifier collision: ${(data.detail as string) ?? "another security already uses this ISIN/CUSIP/SEDOL"}. Edit the value and try again.`,
          );
        } else {
          // etag_conflict — concurrent edit
          setSaveConflict(true);
        }
      } else if (res.status === 422) {
        setSaveError(`Validation error: ${(data.detail ?? data.error) as string ?? "invalid field value"}`);
      } else if (res.status === 400) {
        setSaveError(`Bad request: ${(data.error as string) ?? "check field values"}`);
      } else {
        setSaveError(`Error ${res.status}: ${(data.error as string) ?? "could not save"}`);
      }
    } catch {
      setSaveError("Network error — could not reach the server.");
    } finally {
      setSaving(false);
    }
  }, [form, security, symbol]);

  const reloadSecurity = useCallback(async () => {
    try {
      const res = await fetch(`/api/symbols/${encodeURIComponent(symbol)}/detail`);
      const data = await res.json().catch(() => ({})) as { security?: SecurityMasterInfo };
      if (data.security) {
        setSecurity(data.security);
        setForm(initForm(data.security));
        setDirty(false);
        setSaveConflict(false);
      }
    } catch { /* ignore — user can refresh the page */ }
  }, [symbol]);

  const runEnrichment = useCallback(async () => {
    setRefreshing(true);
    setRefreshStatus(null);
    try {
      const res = await fetch(`/api/symbols/${encodeURIComponent(symbol)}/enrichment/refresh`, {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      const data = await res.json().catch(() => ({ status: "error", detail: "Malformed response" })) as {
        status: "ok" | "error";
        enrichment?: Enrichment;
        detail?: string;
      };
      if (data.status === "ok" && data.enrichment) {
        setEnrichment(data.enrichment);
      }
      setRefreshStatus({ ok: data.status === "ok", detail: data.detail });
    } catch (err) {
      setRefreshStatus({ ok: false, detail: err instanceof Error ? err.message : "Network error" });
    } finally {
      setRefreshing(false);
    }
  }, [symbol]);

  const updateToggle = useCallback(async (flag: string, value: boolean) => {
    setToggling(flag);
    setToggleError(null);
    try {
      const res = await fetch(`/api/symbols/${encodeURIComponent(symbol)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ [flag]: value }),
      });
      if (res.ok) {
        setToggles((prev) => ({ ...prev, [flag]: value }));
      } else {
        const data = await res.json().catch(() => ({})) as { error?: string; detail?: string };
        setToggleError(data.detail ?? data.error ?? `Failed to update ${flag}`);
      }
    } catch {
      setToggleError(`Network error updating ${flag}`);
    } finally {
      setToggling(null);
    }
  }, [symbol]);

  const ticker = security.ticker ?? security.security_id?.split(":")[1] ?? security.security_id;
  const nonUsFlagReason = "US options (XNYS/XNAS only) — not available for this exchange";

  return (
    <section aria-label="Symbol Configuration" className="rounded-[var(--radius)] border border-border bg-bg-card divide-y divide-border/60">
      {/* ── Header ─────────────────────────────────────────────────── */}
      <div className="px-4 py-3 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-text">Symbol Configuration</h3>
        {security.updated_at && (
          <span className="text-xs text-text-muted">Last edited {timeAgoShort(security.updated_at)}</span>
        )}
      </div>

      {/* ── Read-only identity ─────────────────────────────────────── */}
      <div className="px-4 py-4 space-y-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Identity (read-only)</p>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          <ReadOnlyField label="Security ID" value={ro(security.security_id)} />
          <ReadOnlyField label="Exchange MIC" value={ro(security.exchange_mic)} />
          <ReadOnlyField label="Ticker" value={ro(ticker)} />
        </div>
        <p className="text-xs text-text-muted">
          To correct security identity (security_id, MIC, or ticker), contact an operator — a{" "}
          <a
            href="https://github.com"
            className="underline hover:text-accent-blue focus-visible:ring-1 focus-visible:ring-accent-blue/60 outline-none rounded"
            target="_blank"
            rel="noopener noreferrer"
          >
            migration/repair workflow
          </a>{" "}
          is required to safely repoint ledger and configuration references.
        </p>
      </div>

      {/* ── Editable metadata ──────────────────────────────────────── */}
      <div className="px-4 py-4 space-y-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Metadata</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <FieldLabel htmlFor="cfg-company-name">Company Name</FieldLabel>
            <TextInput id="cfg-company-name" value={form.company_name} onChange={(v) => patch("company_name", v)} />
          </div>
          <div>
            <FieldLabel htmlFor="cfg-listing-currency">Listing Currency (ISO 4217)</FieldLabel>
            <TextInput id="cfg-listing-currency" value={form.listing_currency} onChange={(v) => patch("listing_currency", v)} placeholder="EUR" monospace />
          </div>
          <div>
            <FieldLabel htmlFor="cfg-isin">ISIN</FieldLabel>
            <TextInput id="cfg-isin" value={form.isin} onChange={(v) => patch("isin", v)} placeholder="e.g. GB00B10RZP78" monospace />
          </div>
          <div>
            <FieldLabel htmlFor="cfg-cusip">CUSIP</FieldLabel>
            <TextInput id="cfg-cusip" value={form.cusip} onChange={(v) => patch("cusip", v)} placeholder="optional" monospace />
          </div>
          <div>
            <FieldLabel htmlFor="cfg-sedol">SEDOL</FieldLabel>
            <TextInput id="cfg-sedol" value={form.sedol} onChange={(v) => patch("sedol", v)} placeholder="optional" monospace />
          </div>
          <div>
            <FieldLabel htmlFor="cfg-country">Country</FieldLabel>
            <TextInput id="cfg-country" value={form.country} onChange={(v) => patch("country", v)} placeholder="Not set" />
          </div>
          <div>
            <FieldLabel htmlFor="cfg-asset-class">Asset Class</FieldLabel>
            <TextInput id="cfg-asset-class" value={form.asset_class} onChange={(v) => patch("asset_class", v)} placeholder="Equity" />
          </div>
        </div>

        {/* Provider symbol overrides */}
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Provider Symbol Overrides</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <FieldLabel htmlFor="cfg-yfinance">Yahoo Finance override</FieldLabel>
              <TextInput id="cfg-yfinance" value={form.yfinance} onChange={(v) => patch("yfinance", v)} placeholder="e.g. UNA.AS" monospace />
            </div>
            <div>
              <FieldLabel htmlFor="cfg-tradingview">TradingView override</FieldLabel>
              <TextInput id="cfg-tradingview" value={form.tradingview} onChange={(v) => patch("tradingview", v)} placeholder="e.g. EURONEXT-UNA" monospace />
            </div>
          </div>
        </div>

        {/* Effective resolved values */}
        <div className="rounded-[var(--radius)] bg-bg-input border border-border/60 px-3 py-2.5 space-y-1">
          <p className="text-xs font-semibold text-text-muted mb-1.5">Effective resolved values (computed by backend)</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 text-xs">
            <div className="flex items-baseline gap-2">
              <span className="text-text-muted shrink-0">Yahoo Finance:</span>
              <span className="font-mono text-text">{ro(security.effective_yfinance_symbol, "not resolved")}</span>
            </div>
            <div className="flex items-baseline gap-2">
              <span className="text-text-muted shrink-0">TradingView:</span>
              <span className="font-mono text-text">{ro(security.effective_tradingview_symbol, "not resolved")}</span>
            </div>
          </div>
        </div>

        {/* Save actions */}
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={save}
            disabled={saving || !dirty}
            className="inline-flex items-center gap-2 rounded-[var(--radius)] border border-accent-blue/40 bg-accent-blue/10 px-4 py-1.5 text-sm font-medium text-accent-blue transition hover:bg-accent-blue/20 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/60"
          >
            {saving ? "Saving…" : "Save"}
          </button>
          {saveSuccess && (
            <span className="text-xs text-accent-green" role="status">✓ Saved</span>
          )}
          {saveError && (
            <span className="text-xs text-accent-red" role="alert">⚠ {saveError}</span>
          )}
          {saveConflict && (
            <span className="text-xs text-accent-orange flex items-center gap-2" role="alert">
              ⚠ Document changed by another session.{" "}
              <button
                type="button"
                onClick={reloadSecurity}
                className="underline hover:text-accent-blue focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-blue/60 rounded"
              >
                Reload latest
              </button>
            </span>
          )}
        </div>
      </div>

      {/* ── Enrichment status ──────────────────────────────────────── */}
      <div className="px-4 py-4 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Enrichment Status</p>
          <button
            type="button"
            onClick={runEnrichment}
            disabled={refreshing}
            aria-busy={refreshing}
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border border-border px-3 py-1 text-xs text-text-muted hover:text-text transition disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/60"
          >
            {refreshing ? "Running…" : "Re-run enrichment"}
          </button>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-xs">
          <div>
            <div className="text-text-muted">Last updated</div>
            <div className="font-mono text-text mt-0.5">{timeAgoShort(enrichment.last_updated)}</div>
          </div>
          <div>
            <div className="text-text-muted">DGI score</div>
            <div className="font-mono text-text mt-0.5">
              {enrichment.quality_score != null ? enrichment.quality_score.toFixed(1) : "—"}
            </div>
          </div>
          <div>
            <div className="text-text-muted">Category</div>
            <div className="text-text mt-0.5">{ro(enrichment.category)}</div>
          </div>
          <div>
            <div className="text-text-muted">Entry tag</div>
            <div className="text-text mt-0.5">{ro(enrichment.entry_tag)}</div>
          </div>
          <div>
            <div className="text-text-muted">Momentum</div>
            <div className="text-text mt-0.5">{ro(enrichment.momentum)}</div>
          </div>
          <div>
            <div className="text-text-muted">Tech timing</div>
            <div className="font-mono text-text mt-0.5">
              {typeof (enrichment.technicals as { score?: number } | undefined)?.score === "number"
                ? (enrichment.technicals as { score: number }).score.toFixed(1)
                : "—"}
            </div>
          </div>
        </div>

        {refreshStatus && (
          <p
            className={`text-xs rounded-[var(--radius)] px-3 py-2 border ${refreshStatus.ok ? "border-accent-green/30 bg-accent-green/10 text-accent-green" : "border-accent-red/30 bg-accent-red/10 text-accent-red"}`}
            role="status"
          >
            {refreshStatus.ok ? "✓ Enrichment refreshed successfully." : `⚠ Enrichment failed: ${refreshStatus.detail ?? "unknown error"}`}
          </p>
        )}
      </div>

      {/* ── Agent / alert toggles ──────────────────────────────────── */}
      <div className="px-4 py-4 space-y-1">
        <p className="text-xs font-semibold uppercase tracking-wide text-text-muted mb-2">Agent & Alert Toggles</p>
        {!usOptionsEligible && (
          <p className="text-xs text-text-muted mb-3 rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2">
            Covered Calls, Cash-Secured Puts, and Buy Tracker are available for US exchanges (XNYS/XNAS) only. This security ({security.exchange_mic}) is not eligible.
          </p>
        )}
        <ToggleRow
          id="cfg-toggle-cc"
          label="Covered Calls agent"
          checked={toggles.covered_call}
          onChange={(v) => updateToggle("covered_call", v)}
          disabled={!usOptionsEligible || toggling !== null}
          disabledReason={!usOptionsEligible ? nonUsFlagReason : undefined}
        />
        <ToggleRow
          id="cfg-toggle-csp"
          label="Cash-Secured Puts agent"
          checked={toggles.cash_secured_put}
          onChange={(v) => updateToggle("cash_secured_put", v)}
          disabled={!usOptionsEligible || toggling !== null}
          disabledReason={!usOptionsEligible ? nonUsFlagReason : undefined}
        />
        <ToggleRow
          id="cfg-toggle-buy"
          label="Buy Tracker agent"
          checked={toggles.buy_tracker}
          onChange={(v) => updateToggle("buy_tracker", v)}
          disabled={!usOptionsEligible || toggling !== null}
          disabledReason={!usOptionsEligible ? nonUsFlagReason : undefined}
        />
        <ToggleRow
          id="cfg-toggle-telegram"
          label="Telegram notifications"
          checked={toggles.telegram}
          onChange={(v) => updateToggle("telegram_notifications_enabled", v)}
          disabled={toggling !== null}
        />
        {toggleError && (
          <p className="text-xs text-accent-red pt-1" role="alert">⚠ {toggleError}</p>
        )}
      </div>
    </section>
  );
}
