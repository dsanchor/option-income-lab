"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import type { DashboardStatusPayload } from "@/types/dashboard";

/**
 * Transparent background auto-refresh. Renders nothing and no UI.
 *
 * Instead of blindly re-fetching the whole dashboard on a timer, it polls a
 * cheap `/api/dashboard/status` endpoint (per-agent last_run + latest activity
 * timestamp). Only when that signature changes does it call router.refresh(),
 * which re-runs the server component and pulls the new data in place.
 *
 * Pauses while the tab is hidden; re-checks immediately on re-focus.
 */
export default function AutoRefresh({ intervalMs = 30000 }: { intervalMs?: number }) {
  const router = useRouter();
  const sigRef = useRef<string | null>(null);

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null;
    let aborted = false;

    const check = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const res = await fetch("/api/dashboard/status", { cache: "no-store" });
        if (!res.ok) return;
        const data = (await res.json()) as DashboardStatusPayload;
        const sig = JSON.stringify({
          a: data.agents ?? {},
          s: data.agent_statuses ?? {},
          g: data.monitor_agent_enabled ?? {},
          l: data.latest_activity ?? null,
        });
        if (aborted) return;
        // First poll: record the baseline without refreshing.
        if (sigRef.current === null) {
          sigRef.current = sig;
          return;
        }
        if (sig !== sigRef.current) {
          sigRef.current = sig;
          router.refresh();
        }
      } catch {
        /* transient network error — ignore, try again next tick */
      }
    };

    const start = () => {
      if (!timer) timer = setInterval(check, intervalMs);
    };
    const stop = () => {
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        check();
        start();
      } else {
        stop();
      }
    };

    check();
    start();
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      aborted = true;
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [router, intervalMs]);

  return null;
}
