"use client";

import { useEffect, useRef, useState } from "react";
import { renderMarkdown } from "@/lib/markdown";

type Mode = "portfolio" | "quick-analysis";
type Phase = "select" | "portfolio-config" | "quick-config" | "chat";
type ChatMessage = { role: "user" | "assistant"; content: string; ephemeral?: boolean };

interface AgentDef {
  value: string;
  label: string;
  icon: string;
  desc: string;
}

const PORTFOLIO_AGENTS: AgentDef[] = [
  { value: "open_call_monitor", label: "Open Call Monitor", icon: "📞", desc: "Live covered-call positions & assignment risk" },
  { value: "open_put_monitor", label: "Open Put Monitor", icon: "🛡️", desc: "Live cash-secured puts & downside exposure" },
  { value: "covered_call", label: "Following · Covered Call", icon: "📈", desc: "Watchlist symbols flagged for covered calls" },
  { value: "cash_secured_put", label: "Following · Cash-Secured Put", icon: "💵", desc: "Watchlist symbols flagged for CSPs" },
  { value: "buy_tracker", label: "Following · Buy Tracker", icon: "🛒", desc: "Symbols tracked for share accumulation" },
];

interface SymbolData {
  symbol: string;
  market: string;
  option_type: string;
  data: Record<string, unknown>;
}

export default function GlobalChatView() {
  const [phase, setPhase] = useState<Phase>("select");
  const [mode, setMode] = useState<Mode | null>(null);

  // Portfolio config
  const [agentChecked, setAgentChecked] = useState<Record<string, boolean>>(
    () => Object.fromEntries(PORTFOLIO_AGENTS.map((a) => [a.value, true])),
  );
  const [activitiesLimit, setActivitiesLimit] = useState(3);
  const [includeSymbolData, setIncludeSymbolData] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);

  // Quick-analysis config
  const [symbol, setSymbol] = useState("");
  const [market, setMarket] = useState("");
  const [optionType, setOptionType] = useState("");
  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [symbolData, setSymbolData] = useState<SymbolData | null>(null);

  // Chat
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [modeLabel, setModeLabel] = useState("");
  const [symbolLabel, setSymbolLabel] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const autoStarted = useRef(false);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, sending]);

  const selectedAgents = PORTFOLIO_AGENTS.filter((a) => agentChecked[a.value]);

  function reset() {
    setPhase("select");
    setMode(null);
    setAgentChecked(Object.fromEntries(PORTFOLIO_AGENTS.map((a) => [a.value, true])));
    setActivitiesLimit(3);
    setIncludeSymbolData(false);
    setConfigError(null);
    setSymbol("");
    setMarket("");
    setOptionType("");
    setFetching(false);
    setFetchError(null);
    setSymbolData(null);
    setMessages([]);
    setInput("");
    setSending(false);
    setChatError(null);
  }

  function selectMode(m: Mode) {
    setMode(m);
    setPhase(m === "portfolio" ? "portfolio-config" : "quick-config");
  }

  // ---- backend history (excludes ephemeral greeting messages) ----
  function historyOf(msgs: ChatMessage[]) {
    return msgs.filter((m) => !m.ephemeral).map((m) => ({ role: m.role, content: m.content }));
  }

  function startPortfolioChat() {
    if (selectedAgents.length < 1) {
      setConfigError("⚠️ Please select at least one agent");
      return;
    }
    if (!Number.isInteger(activitiesLimit) || activitiesLimit < 1) {
      setConfigError("⚠️ Activities limit must be at least 1");
      return;
    }
    setConfigError(null);
    setModeLabel("💼 Portfolio Chat");
    setSymbolLabel(
      `Last ${activitiesLimit} · ${selectedAgents.length} agent(s)` +
        (includeSymbolData ? " · symbol data" : ""),
    );
    setMessages([
      {
        role: "assistant",
        ephemeral: true,
        content:
          "Hello! I'm your Option Income Lab advisor. I have access to your selected agents " +
          `(${selectedAgents.map((a) => a.label).join(", ")}), covering all of your open positions and tracked follow-ups, ` +
          `with up to ${activitiesLimit} recent activities each. ` +
          (includeSymbolData
            ? "Symbol fundamentals, technicals, and quality metrics are included. "
            : "") +
          "Ask me about your positions, follow-ups, risks, or recommended actions.",
      },
    ]);
    setPhase("chat");
  }

  async function fetchAndAnalyze(
    presetSymbol?: string,
    presetMarket?: string,
    presetOption?: string,
  ) {
    const sym = (presetSymbol ?? symbol).trim().toUpperCase();
    const mkt = (presetMarket ?? market).trim().toUpperCase();
    const opt = (presetOption ?? optionType).trim().toLowerCase();
    if (!sym) return setFetchError("⚠️ Please enter a symbol");
    if (!mkt) return setFetchError("⚠️ Please enter a market");
    if (!opt) return setFetchError("⚠️ Please select an option type");

    setFetchError(null);
    setFetching(true);
    try {
      const res = await fetch("/api/chat/fetch-symbol", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: sym, market: mkt, option_type: opt }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.error) {
        setFetchError("⚠️ " + (data.error || "Failed to fetch data"));
        setFetching(false);
        return;
      }
      const sd = data as SymbolData;
      setSymbolData(sd);
      startQuickAnalysisChat(sd);
    } catch (e) {
      setFetchError("⚠️ Network error: " + (e instanceof Error ? e.message : ""));
      setFetching(false);
    }
  }

  function startQuickAnalysisChat(sd: SymbolData) {
    setFetching(false);
    setModeLabel("🔍 Quick Analysis");
    const optLabel = sd.option_type === "call" ? "Call" : "Put";
    setSymbolLabel(`${sd.market}:${sd.symbol} · ${optLabel}`);
    setMessages([
      {
        role: "assistant",
        ephemeral: true,
        content: `I've loaded market data for **${sd.market}:${sd.symbol}**. Analyzing for ${optLabel} options...`,
      },
    ]);
    setPhase("chat");
    void triggerFirstAnalysis(sd);
  }

  async function triggerFirstAnalysis(sd: SymbolData) {
    setSending(true);
    setChatError(null);
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: [],
          mode: "quick-analysis",
          symbol_data: sd,
          first_analysis: true,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.error) {
        setChatError("⚠️ " + (data.error || `HTTP ${res.status}`));
      } else {
        setMessages((m) => [...m, { role: "assistant", content: data.reply ?? "" }]);
      }
    } catch (e) {
      setChatError("⚠️ Network error: " + (e instanceof Error ? e.message : ""));
    } finally {
      setSending(false);
    }
  }

  async function send() {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setChatError(null);
    const next: ChatMessage[] = [...messages, { role: "user", content: text }];
    setMessages(next);
    setSending(true);
    try {
      const payload: Record<string, unknown> = {
        messages: historyOf(next),
        mode,
      };
      if (mode === "quick-analysis") {
        payload.symbol_data = symbolData;
      } else {
        payload.selected_agents = selectedAgents.map((a) => a.value);
        payload.activities_limit = activitiesLimit;
        payload.include_symbol_data = includeSymbolData;
      }
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.error) {
        setChatError("⚠️ " + (data.error || `HTTP ${res.status}`));
      } else {
        setMessages((m) => [...m, { role: "assistant", content: data.reply ?? "" }]);
      }
    } catch (e) {
      setChatError("⚠️ Network error: " + (e instanceof Error ? e.message : ""));
    } finally {
      setSending(false);
    }
  }

  // Auto-start from URL params (e.g., from DGI screener)
  useEffect(() => {
    if (autoStarted.current) return;
    autoStarted.current = true;
    const params = new URLSearchParams(window.location.search);
    if (params.get("mode") === "quick-analysis" && params.get("symbol")) {
      const sym = params.get("symbol") ?? "";
      const mkt = params.get("market") ?? "NYSE";
      const opt = params.get("option_type") ?? "put";
      setSymbol(sym);
      setMarket(mkt);
      setOptionType(opt);
      setMode("quick-analysis");
      setPhase("quick-config");
      void fetchAndAnalyze(sym, mkt, opt);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold">Chat</h1>
        <p className="text-sm text-text-muted">
          Ask questions about your positions and recent analysis.
        </p>
      </div>

      {phase === "select" && (
        <section className="surface overflow-hidden">
          <div className="border-b border-border px-5 py-3">
            <h2 className="text-base font-semibold">Choose Chat Mode</h2>
          </div>
          <div className="grid grid-cols-1 gap-4 p-5 md:grid-cols-2">
            <ModeCard
              icon="💼"
              title="Portfolio Chat"
              desc="Chat about your tracked symbols and recent analysis activities"
              onClick={() => selectMode("portfolio")}
              tone="blue"
            />
            <ModeCard
              icon="🔍"
              title="Quick Analysis"
              desc="Analyze any symbol (not yet tracked) using live market data"
              onClick={() => selectMode("quick-analysis")}
              tone="purple"
            />
          </div>
        </section>
      )}

      {phase === "portfolio-config" && (
        <section className="surface overflow-hidden">
          <div className="flex items-center justify-between border-b border-border px-5 py-3">
            <h2 className="text-base font-semibold">💼 Portfolio Chat</h2>
            <BackBtn onClick={reset} />
          </div>
          <div className="flex flex-col gap-6 px-5 py-5">
            <div>
              <div className="mb-3 flex items-center justify-between">
                <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
                  Features · Agents
                </label>
                <span className="rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-xs text-text-muted">
                  {selectedAgents.length} of {PORTFOLIO_AGENTS.length}
                </span>
              </div>
              <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                {PORTFOLIO_AGENTS.map((a) => {
                  const on = !!agentChecked[a.value];
                  return (
                    <button
                      key={a.value}
                      type="button"
                      onClick={() =>
                        setAgentChecked((prev) => ({ ...prev, [a.value]: !prev[a.value] }))
                      }
                      className={`flex items-start gap-3 rounded-[var(--radius)] border p-3 text-left transition-all ${
                        on
                          ? "border-accent-blue/50 bg-accent-blue/10"
                          : "border-border bg-bg-input hover:border-accent-blue/30 hover:bg-bg-hover"
                      }`}
                    >
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center justify-between gap-2">
                          <span className="truncate text-sm font-medium">{a.label}</span>
                          <span
                            className={`grid h-4 w-4 shrink-0 place-items-center rounded-full border text-[10px] ${
                              on
                                ? "border-accent-blue bg-accent-blue text-white"
                                : "border-border text-transparent"
                            }`}
                          >
                            ✓
                          </span>
                        </span>
                        <span className="mt-0.5 block text-xs text-text-muted">{a.desc}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>

            <div>
              <label className="mb-3 block text-xs font-medium uppercase tracking-wide text-text-muted">
                Context
              </label>
              <div className="flex flex-col gap-3">
                <div className="flex items-center justify-between gap-4 rounded-[var(--radius)] border border-border bg-bg-input px-4 py-3">
                  <div>
                    <div className="text-sm font-medium">Max activities per position/symbol</div>
                    <div className="text-xs text-text-muted">How much recent history to feed the advisor</div>
                  </div>
                  <input
                    type="number"
                    min={1}
                    value={activitiesLimit}
                    onChange={(e) => setActivitiesLimit(Number(e.target.value))}
                    className="w-20 rounded-[var(--radius)] border border-border bg-bg-card px-3 py-2 text-center text-sm"
                  />
                </div>
                <button
                  type="button"
                  onClick={() => setIncludeSymbolData((v) => !v)}
                  className={`flex items-center justify-between gap-4 rounded-[var(--radius)] border px-4 py-3 text-left transition-all ${
                    includeSymbolData
                      ? "border-accent-blue/50 bg-accent-blue/10"
                      : "border-border bg-bg-input hover:bg-bg-hover"
                  }`}
                >
                  <div>
                    <div className="text-sm font-medium">Include symbol data</div>
                    <div className="text-xs text-text-muted">Fundamentals, technicals & quality scores</div>
                  </div>
                  <span
                    className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${
                      includeSymbolData ? "bg-accent-blue" : "bg-bg-card"
                    }`}
                  >
                    <span
                      className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${
                        includeSymbolData ? "left-[18px]" : "left-0.5"
                      }`}
                    />
                  </span>
                </button>
              </div>
            </div>

            <div>
              <button
                type="button"
                onClick={startPortfolioChat}
                disabled={selectedAgents.length < 1 || activitiesLimit < 1}
                className="rounded-[var(--radius-pill)] bg-accent-blue px-5 py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                Start Chat
              </button>
            </div>
            {configError && (
              <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-2 text-sm">
                {configError}
              </div>
            )}
          </div>
        </section>
      )}

      {phase === "quick-config" && (
        <section className="rounded-[var(--radius)] border border-border bg-bg-card">
          <div className="flex items-center justify-between border-b border-border px-5 py-3">
            <h2 className="text-base font-semibold">🔍 Quick Analysis</h2>
            <BackBtn onClick={reset} />
          </div>
          <div className="flex flex-col gap-4 px-5 py-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div>
                <label className="mb-1 block text-sm text-text-muted">Symbol</label>
                <input
                  type="text"
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                  placeholder="e.g., AAPL"
                  className="w-full rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm uppercase"
                />
              </div>
              <div>
                <label className="mb-1 block text-sm text-text-muted">Market</label>
                <input
                  type="text"
                  value={market}
                  onChange={(e) => setMarket(e.target.value.toUpperCase())}
                  placeholder="e.g., NASDAQ"
                  className="w-full rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm uppercase"
                />
              </div>
              <div>
                <label className="mb-1 block text-sm text-text-muted">Option Type</label>
                <select
                  value={optionType}
                  onChange={(e) => setOptionType(e.target.value)}
                  className="w-full rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm"
                >
                  <option value="">Select...</option>
                  <option value="call">Call</option>
                  <option value="put">Put</option>
                </select>
              </div>
            </div>
            <div>
              <button
                type="button"
                onClick={() => fetchAndAnalyze()}
                disabled={!symbol.trim() || !market.trim() || !optionType || fetching}
                className="rounded-[var(--radius-pill)] bg-accent-blue px-5 py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                Fetch &amp; Analyze
              </button>
            </div>
            {fetchError && (
              <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-2 text-sm">
                {fetchError}
              </div>
            )}
            {fetching && (
              <div className="flex items-center gap-3 rounded-[var(--radius)] bg-bg-input px-4 py-3 text-sm text-text-muted">
                <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-text-muted border-t-transparent" />
                Fetching market data...
              </div>
            )}
          </div>
        </section>
      )}

      {phase === "chat" && (
        <section className="flex flex-col rounded-[var(--radius)] border border-border bg-bg-card">
          <div className="flex items-center justify-between border-b border-border px-5 py-3">
            <div>
              <span className="font-semibold">{modeLabel}</span>
              {symbolLabel && (
                <span className="ml-2 text-sm text-text-muted">{symbolLabel}</span>
              )}
            </div>
            <BackBtn onClick={reset} />
          </div>

          <div ref={scrollRef} className="h-[52vh] space-y-3 overflow-y-auto px-4 py-4">
            {messages.map((m, i) => (
              <div key={i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
                <div
                  className={`max-w-[85%] rounded-[var(--radius)] px-4 py-2.5 text-sm ${
                    m.role === "user"
                      ? "bg-accent-blue text-white"
                      : "border border-border bg-bg-input text-text"
                  }`}
                >
                  {m.role === "assistant" ? (
                    <div
                      className="leading-relaxed [&_strong]:text-text"
                      dangerouslySetInnerHTML={{ __html: renderMarkdown(m.content) }}
                    />
                  ) : (
                    m.content
                  )}
                </div>
              </div>
            ))}
            {sending && <p className="text-sm text-text-muted">…thinking</p>}
          </div>

          {chatError && (
            <div className="mx-4 mb-2 rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-2 text-sm">
              {chatError}
            </div>
          )}

          <form
            onSubmit={(e) => {
              e.preventDefault();
              send();
            }}
            className="flex items-center gap-2 border-t border-border px-4 py-3"
          >
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask a question..."
              disabled={sending}
              className="flex-1 rounded-[var(--radius-pill)] border border-border bg-bg-input px-4 py-2.5 text-sm text-text outline-none focus:border-accent-blue disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={sending || !input.trim()}
              className="rounded-[var(--radius-pill)] bg-accent-blue px-5 py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
            >
              Send
            </button>
          </form>
        </section>
      )}
    </div>
  );
}

function ModeCard({
  icon,
  title,
  desc,
  onClick,
  tone = "blue",
}: {
  icon: string;
  title: string;
  desc: string;
  onClick: () => void;
  tone?: "blue" | "purple";
}) {
  const t =
    tone === "purple"
      ? { bar: "var(--grad-purple)", glow: "rgba(167,139,250,0.14)" }
      : { bar: "var(--grad-blue)", glow: "rgba(91,97,255,0.14)" };
  return (
    <button
      type="button"
      onClick={onClick}
      className="surface card-hover group relative flex h-full flex-col overflow-hidden p-6 text-left"
      style={{
        background: `radial-gradient(120% 120% at 100% 0%, ${t.glow}, transparent 55%), linear-gradient(180deg, var(--bg-card), var(--bg-card-2))`,
      }}
    >
      <span className="absolute inset-y-0 left-0 w-1" style={{ background: t.bar }} aria-hidden />
      <span
        className="mb-4 grid h-12 w-12 place-items-center rounded-[14px] text-2xl shadow-[var(--shadow-glow-blue)] transition-transform group-hover:scale-105"
        style={{ background: t.bar, color: "#fff" }}
      >
        {icon}
      </span>
      <h3 className="mb-1 flex items-center gap-1.5 text-base font-semibold">
        {title}
        <span className="text-text-muted transition-transform group-hover:translate-x-1">→</span>
      </h3>
      <p className="text-sm text-text-muted">{desc}</p>
    </button>
  );
}

function BackBtn({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-[var(--radius-pill)] border border-border bg-bg-input px-3 py-1 text-sm text-text-muted transition hover:bg-hover"
    >
      ← Back
    </button>
  );
}
