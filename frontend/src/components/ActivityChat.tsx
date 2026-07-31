"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { renderMarkdown } from "@/lib/markdown";

type ChatMessage = { role: "user" | "assistant"; content: string };

export default function ActivityChat({
  activityId,
  symbol,
  displayName,
}: {
  activityId: string;
  symbol: string;
  displayName: string;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, sending]);

  async function send() {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setError(null);
    const history = messages;
    setMessages((m) => [...m, { role: "user", content: text }]);
    setSending(true);
    try {
      const res = await fetch(`/api/activities/${encodeURIComponent(activityId)}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history }),
      });
      const data = (await res.json().catch(() => ({}))) as { answer?: string; error?: string };
      if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
      setMessages((m) => [...m, { role: "assistant", content: data.answer ?? "(no response)" }]);
    } catch (err) {
      // Roll back the failed user message
      setMessages((m) => m.slice(0, -1));
      setInput(text);
      setError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <Link href={`/activities/${encodeURIComponent(activityId)}`} className="text-sm text-text-muted hover:text-text">
          ← Activity Detail
        </Link>
        <h1 className="mt-1 text-2xl font-semibold">💬 Discuss Activity</h1>
        <p className="text-sm text-text-muted">
          Ask about this {displayName} activity — uses live option chain, technicals, and the linked position.
        </p>
      </div>

      {error && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {error}
        </div>
      )}

      <div className="flex flex-col gap-3">
        <div
          ref={scrollRef}
          className="h-[52vh] space-y-3 overflow-y-auto rounded-[var(--radius)] border border-border bg-bg-card px-4 py-4"
        >
          {messages.length === 0 && (
            <p className="text-sm text-text-muted">
              Ask anything about this activity — why the agent decided this, the risks, or alternatives for {symbol}.
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
            placeholder="Ask about this activity…"
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
    </div>
  );
}
