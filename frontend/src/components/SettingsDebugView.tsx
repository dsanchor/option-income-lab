"use client";

import { useState } from "react";
import type {
  SettingsDebug,
  FetchPreview,
  FetchPreviewResource,
  AgentChainResult,
  AgentChainStage,
} from "@/types/settings";

const FETCH_ORDER = ["overview", "technicals", "forecast", "dividends", "options_chain"];
const FETCH_LABELS: Record<string, string> = {
  overview: "Overview",
  technicals: "Technicals",
  forecast: "Forecast",
  dividends: "Dividends",
  options_chain: "Options Chain",
};

const ROLL_TYPES = [
  "ROLL_DOWN",
  "ROLL_UP",
  "ROLL_OUT",
  "ROLL_UP_AND_OUT",
  "ROLL_DOWN_AND_OUT",
];

const cardCls = "rounded-[var(--radius)] border border-border bg-bg-card p-5";
const inputCls =
  "rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm text-text focus:border-accent-blue focus:outline-none";
const btnCls =
  "rounded-[var(--radius-pill)] border border-accent-blue/50 bg-accent-blue/15 px-4 py-2 text-sm text-accent-blue transition-colors hover:bg-accent-blue/25 disabled:opacity-50";
const preCls =
  "m-0 max-h-[350px] overflow-auto whitespace-pre-wrap break-words rounded-[var(--radius)] border border-border bg-bg p-3 font-mono text-[0.78rem] leading-relaxed";

function formatSize(chars: number): string {
  if (chars > 1000) return `${(chars / 1000).toFixed(1)}K`;
  return String(chars);
}

function DataFetchTest({ symbols }: { symbols: SettingsDebug["symbols"] }) {
  const [symbol, setSymbol] = useState("");
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("");
  const [statusOk, setStatusOk] = useState(true);
  const [resources, setResources] = useState<Record<string, FetchPreviewResource> | null>(null);

  async function run() {
    if (!symbol) return;
    setLoading(true);
    setStatus("");
    setResources(null);
    const start = Date.now();
    try {
      const res = await fetch(`/api/symbols/${symbol}/fetch-preview`);
      const data: FetchPreview & { error?: string } = await res.json();
      const elapsed = ((Date.now() - start) / 1000).toFixed(1);
      if (!res.ok) {
        setStatusOk(false);
        setStatus(`❌ ${data.error || "Unknown error"}`);
        return;
      }
      setStatusOk(true);
      setStatus(`✅ Done in ${elapsed}s`);
      setResources(data.resources);
    } catch (e) {
      setStatusOk(false);
      setStatus(`❌ Network error: ${e instanceof Error ? e.message : ""}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={cardCls}>
      <h2 className="mb-4 text-lg font-semibold">📈 Data Fetch Test</h2>
      <p className="mb-3 text-sm text-text-muted">
        Test the yfinance data fetcher for any symbol. Fetches live data (~2-5s).
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <select
          className={`${inputCls} w-[220px]`}
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
        >
          <option value="">— Select symbol —</option>
          {symbols.map((s) => (
            <option key={s.symbol} value={s.symbol}>
              {s.display_name}
            </option>
          ))}
        </select>
        <button className={btnCls} disabled={!symbol || loading} onClick={run}>
          ⟳ Fetch
        </button>
        {loading && <span className="text-sm text-text-muted">Fetching…</span>}
        {status && (
          <span className={`font-mono text-sm ${statusOk ? "text-accent-green" : "text-accent-red"}`}>
            {status}
          </span>
        )}
      </div>
      {resources && (
        <div className="mt-4 space-y-4">
          {FETCH_ORDER.filter((k) => resources[k]).map((key) => {
            const r = resources[key];
            const hasError = r.text?.startsWith("[ERROR");
            return (
              <div key={key} className="rounded-[var(--radius)] border border-border bg-bg-card p-4">
                <div className="mb-2 flex items-center justify-between">
                  <strong>{FETCH_LABELS[key]}</strong>
                  <span className="font-mono text-xs text-text-muted">
                    {formatSize(r.size)} chars ·{" "}
                    {r.duration_seconds != null ? `${r.duration_seconds.toFixed(1)}s` : ""}
                    {hasError && <span className="text-accent-red"> · ERROR</span>}
                  </span>
                </div>
                <pre className={preCls}>{r.text}</pre>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Stage({
  title,
  stage,
  highlight,
  defaultCollapsed,
}: {
  title: string;
  stage: AgentChainStage;
  highlight?: boolean;
  defaultCollapsed?: boolean;
}) {
  const [open, setOpen] = useState(!defaultCollapsed);
  return (
    <div
      className={`mb-3 rounded-[var(--radius)] border-2 ${
        highlight ? "border-accent-orange bg-accent-orange/10" : "border-border bg-bg-card"
      }`}
    >
      <div
        className="flex cursor-pointer items-center justify-between px-4 py-3"
        onClick={() => setOpen((o) => !o)}
      >
        <strong>{title}</strong>
        {stage.num_expirations !== undefined && (
          <span className="font-mono text-xs text-text-muted">
            {stage.num_expirations} exp, {stage.num_contracts} contracts
          </span>
        )}
      </div>
      {open && (
        <div className="px-4 pb-3">
          <pre className={`${preCls} max-h-[500px]`}>{stage.text}</pre>
        </div>
      )}
    </div>
  );
}

function AgentChainViewer({ symbols }: { symbols: SettingsDebug["symbols"] }) {
  const [symbol, setSymbol] = useState("");
  const [optType, setOptType] = useState("call");
  const [strike, setStrike] = useState("");
  const [expiration, setExpiration] = useState("");
  const [rollType, setRollType] = useState("");
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("");
  const [statusOk, setStatusOk] = useState(true);
  const [result, setResult] = useState<AgentChainResult | null>(null);

  async function run() {
    if (!symbol) return;
    setLoading(true);
    setStatus("");
    setResult(null);
    const params = new URLSearchParams({ option_type: optType });
    if (strike.trim()) params.set("strike", strike.trim());
    if (expiration.trim()) params.set("expiration", expiration.trim());
    if (rollType) params.set("roll_type", rollType);
    try {
      const res = await fetch(`/api/debug/agent-chain/${symbol}?${params.toString()}`);
      const data: AgentChainResult & { error?: string } = await res.json();
      if (!res.ok) {
        setStatusOk(false);
        setStatus(`❌ ${data.error || "Unknown error"}`);
        return;
      }
      const s1 = data.pipeline.stage_1_delta_filtered;
      let ctx = "";
      if (data.position_context) {
        const pc = data.position_context;
        ctx = ` | strike: $${pc.strike} | exp: ${pc.expiration || "n/a"} | roll: ${
          pc.roll_type || "n/a"
        } | price: $${pc.underlying_price} (${pc.underlying_price_source})`;
      }
      setStatusOk(true);
      setStatus(
        `✅ ${s1.num_expirations} exp, ${s1.num_contracts} contracts (cache: ${data.cache_age_seconds}s ago)${ctx}`,
      );
      setResult(data);
    } catch (e) {
      setStatusOk(false);
      setStatus(`❌ Network error: ${e instanceof Error ? e.message : ""}`);
    } finally {
      setLoading(false);
    }
  }

  const pl = result?.pipeline;
  const hasStage4 = !!pl?.stage_4_candidate_table;

  return (
    <div className={cardCls}>
      <h2 className="mb-4 text-lg font-semibold">🤖 Agent Chain Pipeline View</h2>
      <p className="mb-3 text-sm text-text-muted">
        See the full Phase 2 pipeline: delta filter → ±15 strike filter → direction filter →
        pre-computed candidate table.
      </p>
      <div className="mb-2 flex flex-wrap items-center gap-3">
        <select
          className={`${inputCls} w-[220px]`}
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
        >
          <option value="">— Select symbol —</option>
          {symbols.map((s) => (
            <option key={s.symbol} value={s.symbol}>
              {s.display_name}
            </option>
          ))}
        </select>
        <select
          className={`${inputCls} w-[120px]`}
          value={optType}
          onChange={(e) => setOptType(e.target.value)}
        >
          <option value="call">Call</option>
          <option value="put">Put</option>
        </select>
        <button className={btnCls} disabled={!symbol || loading} onClick={run}>
          👁 View Pipeline
        </button>
        {loading && <span className="text-sm text-text-muted">Loading…</span>}
      </div>
      <div className="mb-2 flex flex-wrap items-center gap-3">
        <input
          className={`${inputCls} w-[140px]`}
          placeholder="Strike (e.g. 475)"
          value={strike}
          onChange={(e) => setStrike(e.target.value)}
        />
        <input
          className={`${inputCls} w-[170px]`}
          placeholder="Exp (YYYY-MM-DD)"
          value={expiration}
          onChange={(e) => setExpiration(e.target.value)}
        />
        <select
          className={`${inputCls} w-[200px]`}
          value={rollType}
          onChange={(e) => setRollType(e.target.value)}
        >
          <option value="">(no roll type)</option>
          {ROLL_TYPES.map((rt) => (
            <option key={rt} value={rt}>
              {rt}
            </option>
          ))}
        </select>
      </div>
      <p className="mb-2 text-[0.78rem] text-text-muted opacity-70">
        Strike + Expiration enable position filtering. Roll type enables direction filtering +
        candidate table (what Phase 2 receives).
      </p>
      {status && (
        <p className={`mb-3 font-mono text-sm ${statusOk ? "text-accent-green" : "text-accent-red"}`}>
          {status}
        </p>
      )}
      {pl && (
        <div className="mt-4">
          <Stage
            title="Stage 1: Delta-filtered chain"
            stage={pl.stage_1_delta_filtered}
            defaultCollapsed={hasStage4}
          />
          {pl.stage_2_position_filtered && (
            <Stage
              title="Stage 2: Position-filtered (±15 strikes)"
              stage={pl.stage_2_position_filtered}
              defaultCollapsed={hasStage4}
            />
          )}
          {pl.stage_3_direction_filtered && (
            <Stage
              title={`Stage 3: Direction-filtered (${rollType || ""})`}
              stage={pl.stage_3_direction_filtered}
              defaultCollapsed={hasStage4}
            />
          )}
          {pl.stage_4_candidate_table && (
            <Stage
              title="Stage 4: Pre-computed candidate table (Phase 2 input)"
              stage={pl.stage_4_candidate_table}
              highlight
            />
          )}
        </div>
      )}
    </div>
  );
}

function CacheManagement({ initial }: { initial: SettingsDebug["cache_stats"] }) {
  const [entryCount, setEntryCount] = useState(initial.total_entries);
  const [cacheSymbols, setCacheSymbols] = useState(initial.symbols);
  const [status, setStatus] = useState("");
  const [statusOk, setStatusOk] = useState(true);
  const [busy, setBusy] = useState(false);

  async function clear() {
    setBusy(true);
    setStatus("Clearing…");
    try {
      const res = await fetch("/api/debug/clear-cache", { method: "POST" });
      const data = await res.json();
      if (data.success) {
        setStatusOk(true);
        setStatus(`✅ Cleared ${data.cleared} entries`);
        setEntryCount(0);
        setCacheSymbols([]);
      } else {
        setStatusOk(false);
        setStatus("❌ Failed");
      }
    } catch {
      setStatusOk(false);
      setStatus("❌ Request failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={cardCls}>
      <h2 className="mb-4 text-lg font-semibold">🧹 Cache Management</h2>
      <div className="flex justify-between border-b border-border py-2 text-sm">
        <span className="text-text-muted">Cached entries</span>
        <span className="font-mono">{entryCount}</span>
      </div>
      <div className="flex justify-between border-b border-border py-2 text-sm">
        <span className="text-text-muted">Cached symbols</span>
        <span className="font-mono">
          {cacheSymbols.length > 0 ? (
            cacheSymbols.join(", ")
          ) : (
            <span className="opacity-50">None</span>
          )}
        </span>
      </div>
      <div className="mt-4 flex items-center gap-3">
        <button
          className="rounded-[var(--radius-pill)] border border-accent-red/50 bg-accent-red/15 px-4 py-2 text-sm text-accent-red transition-colors hover:bg-accent-red/25 disabled:opacity-50"
          disabled={busy}
          onClick={clear}
        >
          🗑️ Clear All Caches
        </button>
        {status && (
          <span className={`font-mono text-sm ${statusOk ? "text-accent-green" : "text-accent-red"}`}>
            {status}
          </span>
        )}
      </div>
    </div>
  );
}

export default function SettingsDebugView({ data }: { data: SettingsDebug }) {
  const connected = data.cosmos_status === "Connected";
  return (
    <div className="space-y-6">
      <DataFetchTest symbols={data.symbols} />
      <AgentChainViewer symbols={data.symbols} />
      <CacheManagement initial={data.cache_stats} />

      {/* CosmosDB Connection */}
      <div className={cardCls}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">💾 CosmosDB Connection</h2>
          <span
            className={`rounded-[var(--radius-pill)] border px-3 py-1 text-xs ${
              connected
                ? "border-accent-green/40 bg-accent-green/10 text-accent-green"
                : "border-accent-red/40 bg-accent-red/10 text-accent-red"
            }`}
          >
            {connected ? "✅" : "❌"} {data.cosmos_status}
          </span>
        </div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div>
            <div className="text-sm text-text-muted">Endpoint</div>
            <div className="break-all font-mono text-sm">
              {data.cosmos_endpoint || "Not configured"}
            </div>
          </div>
          <div>
            <div className="text-sm text-text-muted">Database</div>
            <div className="font-mono text-sm">{data.cosmos_database}</div>
          </div>
          {data.cosmos_error && (
            <div className="md:col-span-2">
              <div className="text-sm text-text-muted">Error</div>
              <div className="font-mono text-sm text-accent-red">{data.cosmos_error}</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
