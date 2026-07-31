"use client";

import { useEffect, useRef, useState } from "react";
import { renderMarkdown } from "@/lib/markdown";

type Prefs = { market_data: boolean; positions: boolean; activities: boolean };
type ChatMessage = { role: "user" | "assistant"; content: string };

const PREFS_KEY = "symbolChatPrefs";
const DEFAULT_PREFS: Prefs = { market_data: true, positions: true, activities: true };

interface ContextResponse {
  context?: string;
  display_name?: string;
  cached_resources?: string[];
  error?: string;
}

export default function SymbolChat({ symbol }: { symbol: string }) {
  const [phase, setPhase] = useState<"select" | "chat">("select");
  const [prefs, setPrefs] = useState<Prefs>(DEFAULT_PREFS);
  const [context, setContext] = useState<string>("");
  const [displayName, setDisplayName] = useState<string>(symbol);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loadingContext, setLoadingContext] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(PREFS_KEY);
      if (saved) setPrefs({ ...DEFAULT_PREFS, ...JSON.parse(saved) });
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, sending]);

  function togglePref(key: keyof Prefs) {
    setPrefs((p) => ({ ...p, [key]: !p[key] }));
  }

  async function startChat() {
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
    } catch {
      /* ignore */
    }
    setLoadingContext(true);
    setError(null);
    try {
      const res = await fetch(`/api/symbols/${encodeURIComponent(symbol)}/chat/context`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preferences: prefs }),
      });
      const data = (await res.json().catch(() => ({}))) as ContextResponse;
      if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
      setContext(data.context ?? "");
      setDisplayName(data.display_name || symbol);
      setPhase("chat");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load context");
    } finally {
      setLoadingContext(false);
    }
  }

  async function send() {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setError(null);
    const nextMessages: ChatMessage[] = [...messages, { role: "user", content: text }];
    setMessages(nextMessages);
    setSending(true);
    try {
      const res = await fetch(`/api/symbols/${encodeURIComponent(symbol)}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: nextMessages, context: context || undefined }),
      });
      const data = (await res.json().catch(() => ({}))) as { reply?: string; error?: string };
      if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
      setMessages((m) => [...m, { role: "assistant", content: data.reply ?? "" }]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSending(false);
    }
  }

  const enabled = [
    prefs.market_data && "📊 Data",
    prefs.positions && "📈 Positions",
    prefs.activities && "📋 Activities",
  ].filter(Boolean) as string[];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">💬 {displayName} Chat</h1>
        <p className="text-sm text-text-muted">Ask an options advisor focused on {symbol}.</p>
      </div>

      {error && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {error}
        </div>
      )}

      {phase === "select" ? (
        <div className="mx-auto w-full max-w-[560px] space-y-4 rounded-[var(--radius)] border border-border bg-bg-card px-6 py-6">
          <h2 className="text-lg font-semibold">Context to include</h2>
          <PrefRow
            label="Market data"
            hint="Overview, technicals, forecast, dividends, options chain"
            checked={prefs.market_data}
            onChange={() => togglePref("market_data")}
          />
          <PrefRow
            label="Positions"
            hint="Your open positions for this symbol"
            checked={prefs.positions}
            onChange={() => togglePref("positions")}
          />
          <PrefRow
            label="Recent activities"
            hint="The latest agent activities and alerts"
            checked={prefs.activities}
            onChange={() => togglePref("activities")}
          />
          <button
            type="button"
            onClick={startChat}
            disabled={loadingContext}
            className="w-full rounded-[var(--radius-pill)] bg-accent-blue px-4 py-3 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
          >
            {loadingContext ? "Loading context…" : "Start chat"}
          </button>
          <p className="text-center text-xs text-text-muted">
            Your preferences are saved for future sessions.
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-text-muted">
            <span>Context: {enabled.length ? enabled.join(" · ") : "none"}</span>
            <button
              type="button"
              onClick={() => setPhase("select")}
              className="rounded-[var(--radius-pill)] border border-border bg-bg-input px-3 py-1 text-text transition hover:bg-bg-hover"
            >
              ↻ Change context
            </button>
          </div>

          <div
            ref={scrollRef}
            className="h-[52vh] space-y-3 overflow-y-auto rounded-[var(--radius)] border border-border bg-bg-card px-4 py-4"
          >
            {messages.length === 0 && (
              <p className="text-sm text-text-muted">
                Ask anything about {symbol} — its options opportunities, risks, positions or market conditions.
              </p>
            )}
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

          <form
            onSubmit={(e) => {
              e.preventDefault();
              send();
            }}
            className="flex items-center gap-2"
          >
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={`Ask about ${symbol}…`}
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
        </div>
      )}
    </div>
  );
}

function PrefRow({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: () => void;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3 rounded-[var(--radius)] border border-border bg-bg-input px-4 py-3">
      <input type="checkbox" checked={checked} onChange={onChange} className="mt-0.5 h-4 w-4 cursor-pointer" />
      <span>
        <span className="block text-sm text-text">{label}</span>
        <span className="block text-xs text-text-muted">{hint}</span>
      </span>
    </label>
  );
}
