"use client";

import {
  type KeyboardEvent,
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Bot, ChevronDown, LoaderCircle, Send, X } from "lucide-react";
import { renderMarkdown } from "@/lib/markdown";
import type {
  ForecastChatMessage,
  ForecastChatRange,
  ForecastChatResponse,
} from "@/types/forecasts";

type State = "closed" | "initial-loading" | "ready" | "follow-up-loading" | "initial-error";

function assistantMarkup(content: string): { __html: string } | null {
  try {
    return { __html: renderMarkdown(content) };
  } catch {
    return null;
  }
}

export default function ForecastReportChat({
  symbol,
  range,
  children,
}: {
  symbol: string;
  range: string;
  children: ReactNode;
}) {
  const chatRange = range as ForecastChatRange;
  const reducedMotion = useReducedMotion();
  const [state, setState] = useState<State>("closed");
  const [messages, setMessages] = useState<ForecastChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [initialError, setInitialError] = useState<string | null>(null);
  const [followUpError, setFollowUpError] = useState<string | null>(null);
  const [failedQuestion, setFailedQuestion] = useState<string | null>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const nearBottomRef = useRef(true);
  const initialReportRef = useRef(false);
  const requestRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const opened = state !== "closed";
  const active = state === "initial-loading" || state === "follow-up-loading";

  useEffect(() => () => {
    requestRef.current += 1;
    abortRef.current?.abort();
  }, []);

  useEffect(() => {
    const node = scrollRef.current;
    if (initialReportRef.current) {
      initialReportRef.current = false;
      node?.scrollTo({ top: 0 });
      return;
    }
    if (!nearBottomRef.current) return;
    node?.scrollTo({ top: node.scrollHeight, behavior: reducedMotion ? "auto" : "smooth" });
  }, [messages, state, reducedMotion]);

  async function request(
    body: Record<string, unknown>,
    kind: "initial" | "follow_up",
    question?: string,
  ) {
    const generation = ++requestRef.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    if (kind === "initial") {
      setState("initial-loading");
      setInitialError(null);
    } else {
      setState("follow-up-loading");
      setFollowUpError(null);
    }
    try {
      const response = await fetch(
        `/api/symbols/${encodeURIComponent(symbol)}/forecasts/chat`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: controller.signal,
        },
      );
      const data = (await response.json().catch(() => ({}))) as Partial<ForecastChatResponse>;
      if (!response.ok || data.error || typeof data.reply !== "string") {
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      if (generation !== requestRef.current) return;
      if (kind === "initial") {
        initialReportRef.current = true;
        setMessages([{ role: "assistant", content: data.reply }]);
      } else if (question) {
        setMessages((current) => [
          ...current,
          { role: "user", content: question },
          { role: "assistant", content: data.reply! },
        ]);
        setFailedQuestion(null);
      }
      setState("ready");
      window.setTimeout(() => inputRef.current?.focus(), 0);
    } catch (error) {
      if (controller.signal.aborted || generation !== requestRef.current) return;
      const message = error instanceof Error ? error.message : "Request failed";
      if (kind === "initial") {
        setInitialError(message);
        setState("initial-error");
      } else {
        setFollowUpError(message);
        setFailedQuestion(question ?? null);
        setInput(question ?? "");
        setState("ready");
      }
    }
  }

  function open() {
    if (opened) {
      requestRef.current += 1;
      abortRef.current?.abort();
      setState("closed");
      window.setTimeout(() => toggleRef.current?.focus(), 0);
      return;
    }
    if (messages.length) {
      setState("ready");
      window.setTimeout(() => inputRef.current?.focus(), 0);
      return;
    }
    setState("initial-loading");
    void request({ mode: "initial", range: chatRange }, "initial");
  }

  function send(question = input.trim()) {
    if (!question || active) return;
    setInput("");
    setFailedQuestion(null);
    void request(
      {
        mode: "follow_up",
        range: chatRange,
        message: question,
        history: messages,
      },
      "follow_up",
      question,
    );
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  }

  return (
    <>
      <div className="flex flex-wrap items-center justify-end gap-2">
        {children}
        <button
          ref={toggleRef}
          type="button"
          aria-expanded={opened}
          aria-controls="forecast-report-chat-panel"
          onClick={open}
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border border-border bg-bg-card px-3 py-2 text-xs font-medium text-text transition hover:bg-bg-hover"
        >
          <Bot size={15} aria-hidden />
          Report &amp; Chat
          <ChevronDown
            size={14}
            aria-hidden
            className={`transition-transform ${opened ? "rotate-180" : ""}`}
          />
        </button>
      </div>

      <AnimatePresence initial={false}>
        {opened && (
          <motion.section
            id="forecast-report-chat-panel"
            role="region"
            aria-labelledby="forecast-report-chat-heading"
            initial={reducedMotion ? false : { height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={reducedMotion ? { opacity: 0 } : { height: 0, opacity: 0 }}
            className="col-span-full overflow-hidden rounded-[var(--radius)] border border-border bg-bg-card"
          >
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <div>
                <h2 id="forecast-report-chat-heading" className="font-semibold">
                  {symbol} forecast report
                </h2>
                <p className="text-xs text-text-muted">Selected range: {chatRange}</p>
              </div>
              <button
                type="button"
                aria-label="Close forecast report and chat"
                onClick={open}
                className="rounded-[var(--radius-pill)] p-2 text-text-muted hover:bg-bg-hover hover:text-text"
              >
                <X size={16} aria-hidden />
              </button>
            </div>

            <div
              ref={scrollRef}
              onScroll={(event) => {
                const node = event.currentTarget;
                nearBottomRef.current =
                  node.scrollHeight - node.scrollTop - node.clientHeight < 80;
              }}
              className="max-h-[52vh] min-h-48 space-y-3 overflow-y-auto px-4 py-4 md:max-h-[480px]"
            >
              {messages.map((message, index) => {
                const markup = message.role === "assistant"
                  ? assistantMarkup(message.content)
                  : null;
                return (
                  <div
                    key={`${message.role}-${index}`}
                    className={message.role === "user" ? "flex justify-end" : "flex justify-start"}
                  >
                    <div className={`max-w-[88%] rounded-[var(--radius)] px-4 py-2.5 text-sm ${
                      message.role === "user"
                        ? "whitespace-pre-wrap bg-accent-blue text-white"
                        : "border border-border bg-bg-input text-text"
                    }`}>
                      {markup ? (
                        <div
                          className="leading-relaxed [&_strong]:text-text"
                          dangerouslySetInnerHTML={markup}
                        />
                      ) : message.content}
                    </div>
                  </div>
                );
              })}
              {state === "initial-loading" && (
                <div className="flex items-center gap-2 text-sm text-text-muted">
                  <LoaderCircle size={16} className="animate-spin" aria-hidden />
                  Analyzing forecast history…
                </div>
              )}
              {state === "initial-error" && (
                <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 p-3 text-sm">
                  <p>{initialError}</p>
                  <button
                    type="button"
                    onClick={() => void request({ mode: "initial", range: chatRange }, "initial")}
                    className="mt-2 font-medium text-accent-blue"
                  >
                    Retry report
                  </button>
                </div>
              )}
              {state === "follow-up-loading" && (
                <p className="text-sm text-text-muted">Thinking…</p>
              )}
            </div>

            <div className="border-t border-border p-3">
              <div aria-live="polite" className="mb-2 min-h-5 text-xs text-accent-red">
                {!followUpError && state === "initial-loading" && "Analyzing forecast history…"}
                {!followUpError && state === "follow-up-loading" && "Generating response…"}
                {!followUpError && state === "initial-error" && initialError}
                {followUpError && (
                  <>
                    {followUpError}{" "}
                    <button
                      type="button"
                      onClick={() => failedQuestion && send(failedQuestion)}
                      className="font-semibold text-accent-blue"
                    >
                      Retry
                    </button>
                  </>
                )}
              </div>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  send();
                }}
                className="flex items-end gap-2"
              >
                <label htmlFor="forecast-report-chat-input" className="sr-only">
                  Ask about this forecast history
                </label>
                <textarea
                  ref={inputRef}
                  id="forecast-report-chat-input"
                  rows={2}
                  maxLength={2000}
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={onKeyDown}
                  disabled={active || state === "initial-error"}
                  placeholder={`Ask about ${symbol}'s forecast history…`}
                  className="min-h-11 flex-1 resize-y rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm text-text outline-none focus:border-accent-blue disabled:opacity-50"
                />
                <button
                  type="submit"
                  aria-label="Send forecast question"
                  disabled={active || state === "initial-error" || !input.trim()}
                  className="inline-flex h-11 items-center gap-1.5 rounded-[var(--radius-pill)] bg-accent-blue px-4 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Send size={15} aria-hidden />
                  Send
                </button>
              </form>
            </div>
          </motion.section>
        )}
      </AnimatePresence>
    </>
  );
}
