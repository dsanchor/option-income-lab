"use client";

import { useEffect, useRef, useState } from "react";
import { renderMarkdown } from "@/lib/markdown";

type ChatMessage = { role: "user" | "assistant"; content: string; ephemeral?: boolean };

function greeting(): ChatMessage[] {
  return [{
    role: "assistant",
    ephemeral: true,
    content: "Hello! I'm Chat, your Option Income Lab assistant. Ask me about your " +
      "symbols, portfolio, economics, options, calendar, plans, alerts, or DGI data. " +
      "I'll use the available read-only MCP tools when needed and tell you if a source is unavailable.",
  }];
}

export default function GlobalChatView() {
  const [messages, setMessages] = useState<ChatMessage[]>(greeting);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const requestRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, sending]);

  useEffect(() => () => abortRef.current?.abort(), []);

  function reset() {
    requestRef.current += 1;
    abortRef.current?.abort();
    setMessages(greeting());
    setInput("");
    setSending(false);
    setChatError(null);
  }

  async function send() {
    const text = input.trim();
    if (!text || sending) return;
    const requestId = ++requestRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    setInput("");
    setChatError(null);
    const next: ChatMessage[] = [...messages, { role: "user", content: text }];
    setMessages(next);
    setSending(true);
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          mode: "api",
          messages: next.filter((m) => !m.ephemeral).map((m) => ({
            role: m.role, content: m.content,
          })),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (requestId !== requestRef.current) return;
      if (!res.ok || data.error) {
        setChatError("⚠️ " + (data.error || `HTTP ${res.status}`));
      } else {
        setMessages((m) => [...m, { role: "assistant", content: data.reply ?? "" }]);
      }
    } catch (e) {
      if (requestId === requestRef.current && !controller.signal.aborted) {
        setChatError("⚠️ Network error: " + (e instanceof Error ? e.message : ""));
      }
    } finally {
      if (requestId === requestRef.current) setSending(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold">Chat</h1>
        <p className="text-sm text-text-muted">Ask questions about your data and recent analysis.</p>
      </div>
      <section className="flex flex-col rounded-[var(--radius)] border border-border bg-bg-card">
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <span className="font-semibold">🤖 Chat</span>
          <button type="button" onClick={reset}
            className="rounded-[var(--radius-pill)] border border-border bg-bg-input px-3 py-1 text-sm text-text-muted transition hover:bg-hover">
            New conversation
          </button>
        </div>
        <div ref={scrollRef} role="log" aria-label="Chat conversation" aria-live="polite"
          className="h-[52vh] space-y-3 overflow-y-auto px-4 py-4">
          {messages.map((m, i) => (
            <div key={i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
              <div className={`max-w-[85%] rounded-[var(--radius)] px-4 py-2.5 text-sm ${
                m.role === "user" ? "bg-accent-blue text-white" : "border border-border bg-bg-input text-text"
              }`}>
                {m.role === "assistant" ? (
                  <div className="leading-relaxed [&_strong]:text-text"
                    dangerouslySetInnerHTML={{ __html: renderMarkdown(m.content) }} />
                ) : m.content}
              </div>
            </div>
          ))}
          {sending && <p className="text-sm text-text-muted">…thinking</p>}
        </div>
        {chatError && (
          <div role="alert" className="mx-4 mb-2 rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-2 text-sm">
            {chatError}
          </div>
        )}
        <form onSubmit={(e) => { e.preventDefault(); void send(); }}
          className="flex items-center gap-2 border-t border-border px-4 py-3">
          <input type="text" value={input} onChange={(e) => setInput(e.target.value)}
            aria-label="Message" placeholder="Ask a question..." disabled={sending}
            className="flex-1 rounded-[var(--radius-pill)] border border-border bg-bg-input px-4 py-2.5 text-sm text-text outline-none focus:border-accent-blue disabled:opacity-50" />
          <button type="submit" disabled={sending || !input.trim()}
            className="rounded-[var(--radius-pill)] bg-accent-blue px-5 py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50">
            Send
          </button>
        </form>
      </section>
    </div>
  );
}
