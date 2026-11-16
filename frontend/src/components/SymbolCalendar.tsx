import type { SymbolCalendar as Calendar } from "@/types/symbol-detail";

export default function SymbolCalendar({ calendar }: { calendar?: Calendar | null }) {
  const today = Date.parse(new Date().toISOString().slice(0, 10));
  return (
    <section aria-label="Calendar" className="rounded-[var(--radius)] border border-border bg-bg-card p-4">
      <h3 className="mb-3 text-sm font-semibold">Calendar</h3>
      <dl className="grid gap-4 text-sm sm:grid-cols-2">
        {([
          ["earnings", "Earnings"],
          ["ex_dividend", "Ex-dividend"],
        ] as const).map(([key, label]) => {
          const dates = calendar?.[key];
          const next = dates?.next_date;
          const last = dates?.last_date;
          const daysUntil = next ? Math.round((Date.parse(next) - today) / 86_400_000) : null;
          const countdown = daysUntil === null ? null : daysUntil === 0 ? "today"
            : daysUntil > 0 ? `in ${daysUntil} ${daysUntil === 1 ? "day" : "days"}`
              : `${-daysUntil} ${daysUntil === -1 ? "day" : "days"} ago`;
          return (
            <div key={key}>
              <dt className="mb-1 text-text-muted">{label}</dt>
              <dd>
                {next ? (
                  <>
                    Next: <time dateTime={next} className="font-mono">{next}</time>
                    <span className="ml-2 text-text-muted">({countdown})</span>
                  </>
                ) : (
                  <span className="text-text-muted">No next date known at the moment.</span>
                )}
                {last && (
                  <p className="mt-1">
                    Last: <time dateTime={last} className="font-mono">{last}</time>
                  </p>
                )}
              </dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}
