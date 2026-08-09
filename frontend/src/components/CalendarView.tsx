"use client";

import { useEffect, useMemo, useState } from "react";
import type { CalendarEvent, CalendarRefreshResponse, CalendarResponse } from "@/types/calendar";

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function eventClass(ev: CalendarEvent): string {
  if (ev.type === "earnings") {
    return ev.has_active_position
      ? "bg-[#9c27b0] text-white"
      : "bg-accent-orange text-black";
  }
  return ev.has_active_position
    ? "bg-accent-red text-white"
    : "bg-[#fbbf24] text-black";
}

function eventLabel(ev: CalendarEvent): string {
  return `${ev.type === "earnings" ? "📊" : "💰"} ${ev.symbol}`;
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-text-muted">
      <span className="inline-block h-3 w-3 rounded-sm" style={{ background: color }} />
      <span>{label}</span>
    </div>
  );
}

export default function CalendarView() {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth()); // 0-indexed
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshLabel, setRefreshLabel] = useState("🔄 Refresh");
  const [refreshing, setRefreshing] = useState(false);

  async function loadEvents() {
    setError(null);
    try {
      const res = await fetch("/api/calendar");
      const body = (await res.json()) as CalendarResponse;
      if (!res.ok || body.error) throw new Error(body.error || `HTTP ${res.status}`);
      setEvents(body.events || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load calendar events.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadEvents();
  }, []);

  async function onRefresh() {
    setRefreshing(true);
    setRefreshLabel("⏳ Refreshing…");
    try {
      const res = await fetch("/api/calendar/refresh", { method: "POST" });
      const body = (await res.json()) as CalendarRefreshResponse;
      if (!res.ok || body.error) throw new Error(body.error || `HTTP ${res.status}`);
      setRefreshLabel(`✅ ${body.updated ?? 0} updated`);
      await loadEvents();
    } catch {
      setRefreshLabel("❌ Error");
    } finally {
      setTimeout(() => {
        setRefreshLabel("🔄 Refresh");
        setRefreshing(false);
      }, 2000);
    }
  }

  const eventsByDate = useMemo(() => {
    const map: Record<string, CalendarEvent[]> = {};
    for (const ev of events) {
      (map[ev.date] ??= []).push(ev);
    }
    return map;
  }, [events]);

  const cells = useMemo(() => {
    const firstDay = new Date(year, month, 1);
    const lastDay = new Date(year, month + 1, 0);
    let startDow = firstDay.getDay() - 1; // Monday = 0
    if (startDow < 0) startDow = 6;
    const prevMonthLast = new Date(year, month, 0).getDate();

    const today = new Date();
    const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;

    const out: {
      key: string;
      day: number;
      other: boolean;
      today: boolean;
      events: CalendarEvent[];
    }[] = [];

    for (let i = startDow - 1; i >= 0; i--) {
      out.push({ key: `p${i}`, day: prevMonthLast - i, other: true, today: false, events: [] });
    }
    for (let d = 1; d <= lastDay.getDate(); d++) {
      const dateStr = `${year}-${String(month + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
      out.push({
        key: dateStr,
        day: d,
        other: false,
        today: dateStr === todayStr,
        events: eventsByDate[dateStr] ?? [],
      });
    }
    const totalCells = startDow + lastDay.getDate();
    const remaining = (7 - (totalCells % 7)) % 7;
    for (let i = 1; i <= remaining; i++) {
      out.push({ key: `n${i}`, day: i, other: true, today: false, events: [] });
    }
    return out;
  }, [year, month, eventsByDate]);

  function prev() {
    setMonth((m) => {
      if (m === 0) {
        setYear((y) => y - 1);
        return 11;
      }
      return m - 1;
    });
  }
  function next() {
    setMonth((m) => {
      if (m === 11) {
        setYear((y) => y + 1);
        return 0;
      }
      return m + 1;
    });
  }
  function goToday() {
    setYear(new Date().getFullYear());
    setMonth(new Date().getMonth());
  }

  const btn =
    "rounded-[var(--radius)] border border-border bg-bg-input px-3 py-1.5 text-sm text-text hover:border-accent-blue disabled:opacity-60";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">📅 Events Calendar</h1>
        <p className="text-sm text-text-muted">
          Earnings and ex-dividend dates for all tracked symbols.
        </p>
      </div>

      <div className="flex flex-wrap gap-6">
        <LegendDot color="#9c27b0" label="Earnings (active position)" />
        <LegendDot color="var(--accent-orange)" label="Earnings (no active position)" />
        <LegendDot color="var(--accent-red)" label="Ex-Dividend (active position)" />
        <LegendDot color="#fbbf24" label="Ex-Dividend (no active position)" />
      </div>

      {error && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {error}
        </div>
      )}

      <div className="rounded-[var(--radius)] border border-border bg-bg-card p-4">
        <div className="mb-4 flex items-center gap-3 border-b border-border pb-4">
          <button type="button" onClick={prev} className={btn}>◀</button>
          <h2 className="min-w-[180px] text-center text-lg font-medium">
            {MONTH_NAMES[month]} {year}
          </h2>
          <button type="button" onClick={next} className={btn}>▶</button>
          <button type="button" onClick={goToday} className={btn}>Today</button>
          <button type="button" onClick={onRefresh} disabled={refreshing} className={`${btn} ml-auto`}>
            {refreshLabel}
          </button>
        </div>

        {loading ? (
          <div className="py-8 text-center text-text-muted">Loading events…</div>
        ) : (
          <div className="grid grid-cols-7 gap-px overflow-hidden rounded-[var(--radius)] border border-border bg-border">
            {DAY_NAMES.map((d) => (
              <div
                key={d}
                className="bg-bg-input px-2 py-2 text-center text-xs font-semibold uppercase text-text-muted"
              >
                {d}
              </div>
            ))}
            {cells.map((c) => (
              <div
                key={c.key}
                className={`min-h-[100px] bg-bg-card p-2 ${c.other ? "opacity-30" : ""} ${
                  c.today ? "ring-1 ring-inset ring-accent-blue" : ""
                }`}
              >
                <div className="mb-1 text-xs font-medium text-text-muted">{c.day}</div>
                {c.events.map((ev, i) => (
                  <div
                    key={i}
                    title={`${ev.type === "earnings" ? "Earnings" : "Ex-Dividend"}: ${ev.symbol}${
                      ev.has_active_position ? " (ACTIVE POSITION)" : ""
                    }`}
                    className={`mb-0.5 truncate rounded-sm px-1.5 py-0.5 text-[0.7rem] font-medium ${eventClass(ev)}`}
                  >
                    {eventLabel(ev)}
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
