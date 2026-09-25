"use client";

import type { BannerItem } from "@/types/dashboard";

function Group({ items }: { items: BannerItem[] }) {
  return (
    <div className="flex shrink-0 items-center gap-2 pr-2">
      {items.map((b, i) => (
        <div
          key={i}
          title={[b.category, b.symbol].filter(Boolean).join(" · ")}
          className="flex items-center gap-2 whitespace-nowrap rounded-[var(--radius-pill)] border border-border bg-bg-input px-3 py-1.5 text-sm"
        >
          {b.emoji && <span>{b.emoji}</span>}
          {b.symbol && <span className="font-mono font-semibold text-text">{b.symbol}</span>}
          {b.text && <span className="text-text-muted">{b.text}</span>}
        </div>
      ))}
    </div>
  );
}

/**
 * Infinite-scroll ticker of banner items. Two duplicated groups animate
 * with the CSS `marquee` keyframe (translateX 0 → -50%) for a seamless loop.
 * Hover pauses the animation (see `.marquee-mask` in globals.css).
 */
export default function DashboardBanner({
  items,
  sourceAsOf,
  sourceWatermarks,
  sourceCounts,
}: {
  items: BannerItem[];
  sourceAsOf?: string | null;
  sourceWatermarks?: Record<string, string>;
  sourceCounts?: Record<string, number>;
}) {
  if (!items || items.length === 0) return null;
  const asOf = sourceAsOf
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(sourceAsOf))
    : null;
  const activityCount = sourceCounts?.activities ?? 0;
  const marketCount = sourceCounts?.market_snapshots ?? 0;
  const coverage = `${marketCount} market snapshot${marketCount === 1 ? "" : "s"} · ${activityCount} recent activit${activityCount === 1 ? "y" : "ies"}`;
  const watermarkDetails = Object.entries(sourceWatermarks ?? {})
    .map(([source, timestamp]) => `${source}: ${timestamp}`)
    .join("\n");
  return (
    <div
      className="marquee-mask surface overflow-hidden px-0 py-2"
      title={[asOf ? `Data as of ${asOf}` : null, coverage, watermarkDetails]
        .filter(Boolean)
        .join("\n")}
    >
      <div className="marquee-track">
        <Group items={items} />
        <Group items={items} />
      </div>
      <div className="px-3 pt-1 text-right text-xs text-text-muted">
        Data as of {asOf ?? "unavailable"} · {coverage}
      </div>
    </div>
  );
}
