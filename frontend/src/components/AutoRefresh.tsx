"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { createAutoRefreshPoller } from "@/lib/autoRefreshPoller";
import type { DashboardStatusPayload } from "@/types/dashboard";

/**
 * Transparent background auto-refresh. Renders nothing and no UI.
 *
 * Instead of blindly re-fetching the whole dashboard on a timer, it polls a
 * cheap `/api/dashboard/status` endpoint (per-agent last_run and latest
 * activity timestamps). Only when that signature changes does it
 * call router.refresh(), which re-runs the server component and pulls the new
 * data in place.
 *
 * Pauses while the tab is hidden; re-checks immediately on re-focus.
 */
export default function AutoRefresh({ intervalMs = 30000 }: { intervalMs?: number }) {
  const router = useRouter();

  useEffect(() => {
    const poller = createAutoRefreshPoller({
      intervalMs,
      timeoutMs: Math.min(10000, intervalMs),
      poll: async (signal) => {
        if (document.visibilityState !== "visible") return null;
        const res = await fetch("/api/dashboard/status", {
          cache: "no-store",
          signal,
        });
        if (!res.ok) return null;
        const data = (await res.json()) as DashboardStatusPayload;
        return JSON.stringify({
          a: data.agents ?? {},
          s: data.agent_statuses ?? {},
          g: data.monitor_agent_enabled ?? {},
          l: data.latest_activity ?? null,
        });
      },
      onChange: () => {
        router.refresh();
      },
    });

    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        poller.start();
      } else {
        poller.stop();
      }
    };

    if (document.visibilityState === "visible") poller.start();
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      poller.stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [router, intervalMs]);

  return null;
}
