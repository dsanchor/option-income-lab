"use client";

import { useState } from "react";
import type { SettingsConfig } from "@/types/settings";

/** Relative-time formatter for last/next-run ISO strings. */
function formatRelative(isoStr: string): string {
  if (!isoStr) return "";
  const dt = new Date(isoStr);
  if (isNaN(dt.getTime())) return "";
  const deltaMs = dt.getTime() - Date.now();
  const abs = Math.abs(deltaMs);
  const mins = Math.round(abs / 60000);
  const hours = Math.round(abs / 3600000);
  const days = Math.round(abs / 86400000);
  let rel: string;
  if (mins < 1) rel = "just now";
  else if (mins < 60) rel = `${mins}m`;
  else if (hours < 24) rel = `${hours}h`;
  else rel = `${days}d`;
  if (rel === "just now") return "(just now)";
  return deltaMs < 0 ? `(${rel} ago)` : `(in ${rel})`;
}

const inputCls =
  "w-full rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 font-mono text-sm text-text focus:border-accent-blue focus:outline-none";
const labelCls = "mb-1 block text-sm text-text-muted";
const hintCls = "mt-1 block text-xs text-text-muted";
const runBtnCls =
  "rounded-[var(--radius-pill)] border border-accent-blue/50 bg-accent-blue/15 px-4 py-1.5 text-sm text-accent-blue transition-colors hover:bg-accent-blue/25 disabled:opacity-50";

/** Read-only Last Run / Next Run pair. */
function RunTimes({
  last,
  lastIso,
  next,
  nextIso,
}: {
  last: string;
  lastIso: string;
  next: string;
  nextIso: string;
}) {
  return (
    <div className="mt-3 grid grid-cols-2 gap-3">
      <div>
        <label className={labelCls}>Last Run</label>
        <div className="rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 font-mono text-sm text-text-muted">
          {last || "Never"}
          {lastIso && (
            <span className="ml-2 text-xs opacity-70">{formatRelative(lastIso)}</span>
          )}
        </div>
      </div>
      <div>
        <label className={labelCls}>Next Run</label>
        <div className="rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 font-mono text-sm text-text-muted">
          {next || "N/A"}
          {nextIso && (
            <span className="ml-2 text-xs opacity-70">{formatRelative(nextIso)}</span>
          )}
        </div>
      </div>
    </div>
  );
}

/** A titled scheduler-task section with an enable toggle + Run Now control. */
function TaskCard({
  title,
  children,
  runStatus,
}: {
  title: string;
  children: React.ReactNode;
  runStatus?: React.ReactNode;
}) {
  return (
    <div className="mb-6">
      <h3 className="mb-3 border-b border-border pb-2 text-[0.95rem] font-semibold text-text">
        {title}
      </h3>
      {children}
      {runStatus && <div className="mt-3 flex items-center gap-3">{runStatus}</div>}
    </div>
  );
}

function Toggle({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  hint?: string;
}) {
  return (
    <div className="mb-4">
      <label className="flex cursor-pointer items-center gap-2">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
          className="accent-[var(--color-accent-blue)]"
        />
        <span className="text-sm">{label}</span>
      </label>
      {hint && <small className="mt-1 ml-6 block text-xs text-text-muted">{hint}</small>}
    </div>
  );
}

interface RunState {
  msg: string;
  ok: boolean;
}

export default function SettingsConfigView({ initial }: { initial: SettingsConfig }) {
  // Single mutable snapshot of every field. Save posts ALL of it back so no
  // absent key resets a task to disabled/default.
  const [cfg, setCfg] = useState<SettingsConfig>(initial);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<RunState | null>(null);
  const [runStates, setRunStates] = useState<Record<string, RunState>>({});

  function set<K extends keyof SettingsConfig>(key: K, value: SettingsConfig[K]) {
    setCfg((c) => ({ ...c, [key]: value }));
  }

  async function runNow(key: string, endpoint: string) {
    setRunStates((s) => ({ ...s, [key]: { msg: "Running…", ok: true } }));
    try {
      const res = await fetch(endpoint, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        const msg = data.status || data.message || (data.saved ? "Done" : "Triggered ✓");
        setRunStates((s) => ({ ...s, [key]: { msg: `✅ ${msg}`, ok: true } }));
      } else {
        setRunStates((s) => ({
          ...s,
          [key]: { msg: `❌ ${data.error || data.message || "Failed"}`, ok: false },
        }));
      }
    } catch (e) {
      setRunStates((s) => ({
        ...s,
        [key]: { msg: `❌ ${e instanceof Error ? e.message : "Network error"}`, ok: false },
      }));
    }
  }

  async function testTelegram() {
    setRunStates((s) => ({ ...s, telegram: { msg: "Sending…", ok: true } }));
    try {
      const res = await fetch("/api/telegram/test", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (data.ok) {
        setRunStates((s) => ({ ...s, telegram: { msg: "✅ Message sent", ok: true } }));
      } else {
        setRunStates((s) => ({
          ...s,
          telegram: { msg: `❌ ${data.error || "Failed"}`, ok: false },
        }));
      }
    } catch (e) {
      setRunStates((s) => ({
        ...s,
        telegram: { msg: `❌ ${e instanceof Error ? e.message : "Network error"}`, ok: false },
      }));
    }
  }

  async function save() {
    setSaving(true);
    setSaveMsg(null);
    // Full snapshot of every persisted field.
    const payload = {
      monitoring_enabled: cfg.monitoring_enabled,
      cron_expr: cfg.cron_expr,
      summary_enabled: cfg.summary_enabled,
      summary_cron: cfg.summary_cron,
      summary_activity_count: cfg.summary_activity_count,
      banner_enabled: cfg.banner_enabled,
      banner_cron: cfg.banner_cron,
      banner_max_items: cfg.banner_max_items,
      calendar_enabled: cfg.calendar_enabled,
      calendar_cron: cfg.calendar_cron,
      options_chain_enabled: cfg.options_chain_enabled,
      options_chain_cron: cfg.options_chain_cron,
      dgi_enabled: cfg.dgi_enabled,
      dgi_cron: cfg.dgi_cron,
      dgi_symbols: cfg.dgi_symbols,
      dgi_top_n: cfg.dgi_top_n,
      pe_enabled: cfg.pe_enabled,
      pe_cron: cfg.pe_cron,
      pf_enabled: cfg.pf_enabled,
      pf_cron: cfg.pf_cron,
      pf_band_confidence: cfg.pf_band_confidence,
      pf_vol_source: cfg.pf_vol_source,
      pf_trend_window: cfg.pf_trend_window,
      pf_trend_window_long: cfg.pf_trend_window_long,
      plan_monitor_enabled: cfg.plan_monitor_enabled,
      plan_monitor_cron: cfg.plan_monitor_cron,
      telegram_enabled: cfg.telegram_enabled,
      telegram_bot_token: cfg.telegram_bot_token,
      telegram_chat_id: cfg.telegram_chat_id,
    };
    try {
      const res = await fetch("/api/settings/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.success) {
        setSaveMsg({
          msg: `✅ Saved: ${(data.saved || []).join(", ") || "no changes"}`,
          ok: true,
        });
      } else {
        setSaveMsg({ msg: `❌ ${data.error || "Save failed"}`, ok: false });
      }
    } catch (e) {
      setSaveMsg({ msg: `❌ ${e instanceof Error ? e.message : "Network error"}`, ok: false });
    } finally {
      setSaving(false);
    }
  }

  const RunStatus = ({ id, endpoint }: { id: string; endpoint: string }) => (
    <>
      <button type="button" className={runBtnCls} onClick={() => runNow(id, endpoint)}>
        ▶ Run Now
      </button>
      {runStates[id] && (
        <span
          className={`font-mono text-sm ${runStates[id].ok ? "text-accent-green" : "text-accent-red"}`}
        >
          {runStates[id].msg}
        </span>
      )}
    </>
  );

  return (
    <div className="space-y-6">
      <div className="rounded-[var(--radius)] border border-border bg-bg-card p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">⏱️ Scheduler</h2>
          <span className="font-mono text-xs text-text-muted">Server time: {cfg.server_time}</span>
        </div>

        {/* Monitoring Agent */}
        <TaskCard title="Monitoring Agent" runStatus={<RunStatus id="monitoring" endpoint="/api/trigger-all" />}>
          <Toggle
            checked={cfg.monitoring_enabled}
            onChange={(v) => set("monitoring_enabled", v)}
            label="Enable monitoring agent"
            hint="Periodically checks all active position monitors and strategy followers."
          />
          <label className={labelCls}>Cron Expression</label>
          <input
            className={inputCls}
            value={cfg.cron_expr}
            onChange={(e) => set("cron_expr", e.target.value)}
            spellCheck={false}
          />
          <small className={hintCls}>
            Format: <code>minute hour day-of-month month day-of-week</code> — e.g.{" "}
            <code>0 14-21/2 * * 1-5</code> = every 2h on weekdays 14:00–20:00
          </small>
          <RunTimes
            last={cfg.monitoring_last_run}
            lastIso={cfg.monitoring_last_run_iso}
            next={cfg.monitoring_next_run}
            nextIso={cfg.monitoring_next_run_iso}
          />
        </TaskCard>

        {/* Summarization Agent */}
        <TaskCard title="Summarization Agent" runStatus={<RunStatus id="summary" endpoint="/api/trigger/summary_agent" />}>
          <Toggle
            checked={cfg.summary_enabled}
            onChange={(v) => set("summary_enabled", v)}
            label="Enable summary agent"
            hint="Daily portfolio summary sent via Telegram. Requires Telegram notifications to be enabled."
          />
          <div className="mb-3 grid grid-cols-[2fr_1fr] gap-3">
            <div>
              <label className={labelCls}>Cron Expression</label>
              <input
                className={inputCls}
                value={cfg.summary_cron}
                onChange={(e) => set("summary_cron", e.target.value)}
                placeholder="0 8 * * *"
                spellCheck={false}
              />
              <small className={hintCls}>
                Example: <code>0 8 * * *</code> = 8 AM daily
              </small>
            </div>
            <div>
              <label className={labelCls}>Activity Count</label>
              <input
                type="number"
                className={inputCls}
                value={cfg.summary_activity_count}
                min={1}
                max={10}
                onChange={(e) => set("summary_activity_count", Number(e.target.value))}
              />
            </div>
          </div>
          <RunTimes
            last={cfg.summary_last_run}
            lastIso={cfg.summary_last_run_iso}
            next={cfg.summary_next_run}
            nextIso={cfg.summary_next_run_iso}
          />
        </TaskCard>

        {/* Dashboard Banner Agent */}
        <TaskCard title="Dashboard Banner Agent" runStatus={<RunStatus id="banner" endpoint="/api/trigger/banner_agent" />}>
          <Toggle
            checked={cfg.banner_enabled}
            onChange={(v) => set("banner_enabled", v)}
            label="Enable dashboard banner agent"
            hint="Generates daily news/insights banner for the dashboard (earnings proximity, ex-div dates, trend changes, alerts)."
          />
          <div className="mb-3 grid grid-cols-[2fr_1fr] gap-3">
            <div>
              <label className={labelCls}>Cron Expression</label>
              <input
                className={inputCls}
                value={cfg.banner_cron}
                onChange={(e) => set("banner_cron", e.target.value)}
                placeholder="0 5 * * *"
                spellCheck={false}
              />
              <small className={hintCls}>
                Example: <code>0 5 * * *</code> = 5 AM daily
              </small>
            </div>
            <div>
              <label className={labelCls}>Max Items</label>
              <input
                type="number"
                className={inputCls}
                value={cfg.banner_max_items}
                min={3}
                max={20}
                onChange={(e) => set("banner_max_items", Number(e.target.value))}
              />
            </div>
          </div>
          <RunTimes
            last={cfg.banner_last_run}
            lastIso={cfg.banner_last_run_iso}
            next={cfg.banner_next_run}
            nextIso={cfg.banner_next_run_iso}
          />
        </TaskCard>

        {/* Calendar Sync */}
        <TaskCard title="Calendar Sync (Earnings & Ex-Dividend)" runStatus={<RunStatus id="calendar" endpoint="/api/calendar/refresh" />}>
          <Toggle
            checked={cfg.calendar_enabled}
            onChange={(v) => set("calendar_enabled", v)}
            label="Enable calendar sync"
            hint="Fetches earnings and ex-dividend dates from Yahoo Finance and stores them in CosmosDB for the Calendar view."
          />
          <label className={labelCls}>Cron Schedule</label>
          <input
            className={`${inputCls} max-w-[300px]`}
            value={cfg.calendar_cron}
            onChange={(e) => set("calendar_cron", e.target.value)}
            placeholder="0 5 * * 1-5"
            spellCheck={false}
          />
          <RunTimes
            last={cfg.calendar_last_run}
            lastIso={cfg.calendar_last_run_iso}
            next={cfg.calendar_next_run}
            nextIso={cfg.calendar_next_run_iso}
          />
        </TaskCard>

        {/* Options Chain Scheduler */}
        <TaskCard title="Options Chain Scheduler" runStatus={<RunStatus id="options_chain" endpoint="/api/trigger/options_chain" />}>
          <Toggle
            checked={cfg.options_chain_enabled}
            onChange={(v) => set("options_chain_enabled", v)}
            label="Enable options chain scheduler"
            hint="Periodically fetch and cache options chain data for all symbols. Reduces latency for agents and chat queries."
          />
          <label className={labelCls}>Cron Expression</label>
          <input
            className={inputCls}
            value={cfg.options_chain_cron}
            onChange={(e) => set("options_chain_cron", e.target.value)}
            placeholder="0 * * * *"
            spellCheck={false}
          />
          <small className={hintCls}>
            Example: <code>0 * * * *</code> = every hour
          </small>
          <RunTimes
            last={cfg.options_chain_last_run}
            lastIso={cfg.options_chain_last_run_iso}
            next={cfg.options_chain_next_run}
            nextIso={cfg.options_chain_next_run_iso}
          />
        </TaskCard>

        {/* DGI Screener */}
        <TaskCard title="DGI Screener" runStatus={<RunStatus id="dgi" endpoint="/api/trigger/dgi_screener" />}>
          <Toggle
            checked={cfg.dgi_enabled}
            onChange={(v) => set("dgi_enabled", v)}
            label="Enable DGI dividend screener"
            hint="Screens S&P 500 for top dividend growth stocks. Runs independently from the main options scheduler."
          />
          <div className="mb-3">
            <label className={labelCls}>Cron Expression</label>
            <input
              className={inputCls}
              value={cfg.dgi_cron}
              onChange={(e) => set("dgi_cron", e.target.value)}
              placeholder="0 6 * * 1-5"
              spellCheck={false}
            />
            <small className={hintCls}>
              Example: <code>0 6 * * 1-5</code> = 6 AM weekdays
            </small>
          </div>
          <div className="mb-3">
            <label className={labelCls}>Symbols (comma-separated)</label>
            <textarea
              className={`${inputCls} resize-y text-xs`}
              rows={4}
              value={cfg.dgi_symbols}
              onChange={(e) => set("dgi_symbols", e.target.value)}
              spellCheck={false}
            />
            <small className={hintCls}>S&P 500 tickers separated by commas (e.g. AAPL,ABBV,ABT,...)</small>
          </div>
          <div className="mb-3">
            <label className={labelCls}>Number of stocks in Top list</label>
            <input
              type="number"
              className={`${inputCls} w-[120px]`}
              value={cfg.dgi_top_n}
              min={1}
              max={500}
              step={1}
              placeholder="40"
              onChange={(e) => set("dgi_top_n", Number(e.target.value))}
            />
            <small className={hintCls}>Number of top-scoring stocks to include in the DGI list (default: 40)</small>
          </div>
          <RunTimes
            last={cfg.dgi_last_run}
            lastIso={cfg.dgi_last_run_iso}
            next={cfg.dgi_next_run}
            nextIso={cfg.dgi_next_run_iso}
          />
        </TaskCard>

        {/* Watchlist Enrichment */}
        <TaskCard title="Watchlist Enrichment" runStatus={<RunStatus id="pe" endpoint="/api/trigger/portfolio_enrichment" />}>
          <Toggle
            checked={cfg.pe_enabled}
            onChange={(v) => set("pe_enabled", v)}
            label="Enable watchlist enrichment scheduler"
            hint="Periodically enrich watchlist symbols with quality scores, dividend metrics, and technicals."
          />
          <label className={labelCls}>Cron Expression</label>
          <input
            className={inputCls}
            value={cfg.pe_cron}
            onChange={(e) => set("pe_cron", e.target.value)}
            placeholder="0 9-17 * * 1-5"
            spellCheck={false}
          />
          <small className={hintCls}>
            Example: <code>0 9-17 * * 1-5</code> = hourly 9 AM–5 PM weekdays
          </small>
          <RunTimes
            last={cfg.pe_last_run}
            lastIso={cfg.pe_last_run_iso}
            next={cfg.pe_next_run}
            nextIso={cfg.pe_next_run_iso}
          />
        </TaskCard>

        {/* Price Forecast */}
        <TaskCard title="Price Forecast" runStatus={<RunStatus id="pf" endpoint="/api/trigger/price_forecast" />}>
          <Toggle
            checked={cfg.pf_enabled}
            onChange={(v) => set("pf_enabled", v)}
            label="Enable deterministic price-forecast scheduler"
            hint="Daily volatility-cone predictions (1d/1w/2w/4w) self-validated against actual prices. Fully deterministic — no AI/LLM."
          />
          <div className="mb-3">
            <label className={labelCls}>Cron Expression</label>
            <input
              className={inputCls}
              value={cfg.pf_cron}
              onChange={(e) => set("pf_cron", e.target.value)}
              placeholder="0 21 * * 1-5"
              spellCheck={false}
            />
            <small className={hintCls}>
              Default: <code>0 21 * * 1-5</code> = 9 PM UTC weekdays (after US close)
            </small>
          </div>
          <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-4">
            <div>
              <label className={labelCls} title="Central probability of the primary range. Lower = tighter, more actionable band.">
                Band confidence ⓘ
              </label>
              <select
                className={inputCls}
                value={String(cfg.pf_band_confidence)}
                onChange={(e) => set("pf_band_confidence", Number(e.target.value))}
              >
                <option value="0.5">50% — most likely range (tighter)</option>
                <option value="0.68">68% — classic ±1σ</option>
                <option value="0.8">80%</option>
                <option value="0.95">95% (widest)</option>
              </select>
            </div>
            <div>
              <label className={labelCls} title="HV = flat 20-day realized vol. EWMA = recency-weighted. IV→HV = implied vol from the option chain when available, else HV.">
                Volatility source ⓘ
              </label>
              <select
                className={inputCls}
                value={cfg.pf_vol_source}
                onChange={(e) => set("pf_vol_source", e.target.value)}
              >
                <option value="iv_hv">IV → HV (recommended)</option>
                <option value="ewma">EWMA (responsive)</option>
                <option value="hv">HV (flat window)</option>
              </select>
            </div>
            <div>
              <label className={labelCls} title="Sessions used for the linear-regression trend overlay on the short horizons (1d, 1w).">
                Trend window (short · 1d/1w) ⓘ
              </label>
              <input
                type="number"
                className={inputCls}
                value={cfg.pf_trend_window}
                min={5}
                max={120}
                step={1}
                onChange={(e) => set("pf_trend_window", Number(e.target.value))}
              />
            </div>
            <div>
              <label className={labelCls} title="Sessions used for the linear-regression trend overlay on the long horizons (2w, 4w).">
                Trend window (long · 2w/4w) ⓘ
              </label>
              <input
                type="number"
                className={inputCls}
                value={cfg.pf_trend_window_long}
                min={5}
                max={120}
                step={1}
                onChange={(e) => set("pf_trend_window_long", Number(e.target.value))}
              />
            </div>
          </div>
          <small className="mb-3 block text-[0.72rem] text-text-muted">
            Note: IV is only available for new daily forecasts (not stored historically). Re-run the
            backfill with <code>--force</code> to rebuild history under new settings (uses HV/EWMA).
          </small>
          <RunTimes
            last={cfg.pf_last_run}
            lastIso={cfg.pf_last_run_iso}
            next={cfg.pf_next_run}
            nextIso={cfg.pf_next_run_iso}
          />
        </TaskCard>

        {/* Plan Monitor */}
        <TaskCard title="Plan Monitor" runStatus={<RunStatus id="plan_monitor" endpoint="/api/trigger/plan_monitor" />}>
          <Toggle
            checked={cfg.plan_monitor_enabled}
            onChange={(v) => set("plan_monitor_enabled", v)}
            label="Enable plan monitor agent"
            hint="Monitors action plans and adds agent notes based on current market data. Uses gpt-5.4-mini."
          />
          <label className={labelCls}>Cron Expression</label>
          <input
            className={inputCls}
            value={cfg.plan_monitor_cron}
            onChange={(e) => set("plan_monitor_cron", e.target.value)}
            placeholder="0 4,16 * * 1-5"
            spellCheck={false}
          />
          <small className={hintCls}>
            Default: <code>0 4,16 * * 1-5</code> = 4 AM and 4 PM weekdays
          </small>
          <RunTimes
            last={cfg.plan_monitor_last_run}
            lastIso={cfg.plan_monitor_last_run_iso}
            next={cfg.plan_monitor_next_run}
            nextIso={cfg.plan_monitor_next_run_iso}
          />
        </TaskCard>
      </div>

      {/* Telegram Notifications */}
      <div className="rounded-[var(--radius)] border border-border bg-bg-card p-5">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-lg font-semibold">📬 Telegram Notifications</h2>
          <span
            className={`rounded-[var(--radius-pill)] border px-3 py-1 text-xs ${
              cfg.telegram_enabled
                ? "border-accent-green/40 bg-accent-green/10 text-accent-green"
                : "border-accent-red/40 bg-accent-red/10 text-accent-red"
            }`}
          >
            {cfg.telegram_enabled ? "✅ Enabled" : "❌ Disabled"}
          </span>
        </div>
        <p className="mb-4 text-sm text-text-muted">
          Receive alert notifications via Telegram when agents trigger SELL or ROLL actions. Requires
          a Telegram bot token from{" "}
          <a href="https://t.me/botfather" target="_blank" rel="noreferrer" className="text-accent-blue">
            @BotFather
          </a>
          .
        </p>
        <Toggle
          checked={cfg.telegram_enabled}
          onChange={(v) => set("telegram_enabled", v)}
          label="Enable Telegram notifications"
        />
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div>
            <label className={labelCls}>Bot Token</label>
            <input
              type="password"
              className={inputCls}
              value={cfg.telegram_bot_token}
              onChange={(e) => set("telegram_bot_token", e.target.value)}
              placeholder="123456:ABC-DEF..."
            />
          </div>
          <div>
            <label className={labelCls}>Chat ID</label>
            <input
              className={inputCls}
              value={cfg.telegram_chat_id}
              onChange={(e) => set("telegram_chat_id", e.target.value)}
              placeholder="-1001234567890"
            />
          </div>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            className={runBtnCls}
            onClick={testTelegram}
            disabled={!cfg.telegram_enabled}
          >
            📤 Send Test Message
          </button>
          {runStates.telegram && (
            <span
              className={`font-mono text-sm ${runStates.telegram.ok ? "text-accent-green" : "text-accent-red"}`}
            >
              {runStates.telegram.msg}
            </span>
          )}
        </div>
      </div>

      {/* Save */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          className="rounded-[var(--radius-pill)] border border-accent-green/50 bg-accent-green/15 px-6 py-2 text-sm font-medium text-accent-green transition-colors hover:bg-accent-green/25 disabled:opacity-50"
          onClick={save}
          disabled={saving}
        >
          {saving ? "Saving…" : "💾 Save"}
        </button>
        {saveMsg && (
          <span
            className={`font-mono text-sm ${saveMsg.ok ? "text-accent-green" : "text-accent-red"}`}
          >
            {saveMsg.msg}
          </span>
        )}
      </div>
    </div>
  );
}
