"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

const WATCH_AGENTS = ["covered_call", "cash_secured_put"];
const MONITOR_AGENTS = ["open_call_monitor", "open_put_monitor"];

export default function ActivityActions({
  symbol,
  activityId,
  agentType,
  isAlert,
  validationStatus,
  validationSource,
}: {
  symbol: string;
  activityId: string;
  agentType: string;
  isAlert: boolean;
  validationStatus?: "approved" | "review_incomplete" | "error" | null;
  validationSource?: "best_options" | "options_screener" | null;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const isValidation = validationSource != null;
  const isApprovedValidation = isValidation && validationStatus === "approved" && isAlert;
  const canOpen = isAlert && (WATCH_AGENTS.includes(agentType) || isApprovedValidation);
  const canRoll = isAlert && MONITOR_AGENTS.includes(agentType);

  async function run(
    kind: string,
    url: string,
    method: string,
    confirmText: string,
    successText: string,
    redirect: boolean,
  ) {
    if (!window.confirm(confirmText)) return;
    setBusy(kind);
    setMsg(null);
    try {
      const res = await fetch(url, { method, headers: { Accept: "application/json" } });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setMsg({ kind: "error", text: (data as { error?: string }).error || "Request failed." });
        setBusy(null);
        return;
      }
      setMsg({ kind: "ok", text: successText });
      if (redirect) {
        setTimeout(() => {
          router.push(`/symbols/${encodeURIComponent(symbol)}`);
          router.refresh();
        }, 900);
      } else {
        setBusy(null);
      }
    } catch (e) {
      setMsg({ kind: "error", text: e instanceof Error ? e.message : "Network error" });
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {canOpen && (
          <button
            type="button"
            disabled={busy !== null}
            onClick={() =>
              run(
                "open",
                `/api/symbols/${encodeURIComponent(symbol)}/positions/from-activity/${encodeURIComponent(activityId)}`,
                "POST",
                isValidation
                  ? "Open a position from this validated contract? This requires manual confirmation and will disable the watchlist."
                  : "Open a position from this alert? This will disable the watchlist.",
                "✓ Position opened! Redirecting…",
                true,
              )
            }
            className="rounded-[var(--radius-pill)] bg-accent-blue px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {busy === "open" ? "Opening…" : "Open Position"}
          </button>
        )}
        {canRoll && (
          <button
            type="button"
            disabled={busy !== null}
            onClick={() =>
              run(
                "roll",
                `/api/symbols/${encodeURIComponent(symbol)}/positions/roll-from-activity/${encodeURIComponent(activityId)}`,
                "POST",
                "Roll this position? The current position will be closed and a new one opened.",
                "✓ Position rolled! Redirecting…",
                true,
              )
            }
            className="rounded-[var(--radius-pill)] bg-accent-blue px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {busy === "roll" ? "Rolling…" : "Roll Position"}
          </button>
        )}
        <button
          type="button"
          disabled={busy !== null}
          onClick={() =>
            run(
              "delete",
              `/api/activities/${encodeURIComponent(activityId)}`,
              "DELETE",
              "Are you sure you want to permanently delete this activity?",
              "✓ Deleted. Redirecting…",
              true,
            )
          }
          className="rounded-[var(--radius-pill)] border border-accent-red/50 px-4 py-2 text-sm text-accent-red hover:bg-accent-red/10 disabled:opacity-50"
        >
          {busy === "delete" ? "Deleting…" : "Delete Activity"}
        </button>
      </div>
      {msg && (
        <div
          className={`rounded-[var(--radius)] border px-3 py-2 text-sm ${
            msg.kind === "ok"
              ? "border-accent-green/40 bg-accent-green/10 text-accent-green"
              : "border-accent-red/40 bg-accent-red/10 text-accent-red"
          }`}
        >
          {msg.text}
        </div>
      )}
    </div>
  );
}
