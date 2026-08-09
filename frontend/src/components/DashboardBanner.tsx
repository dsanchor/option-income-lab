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
export default function DashboardBanner({ items }: { items: BannerItem[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="marquee-mask surface overflow-hidden px-0 py-2">
      <div className="marquee-track">
        <Group items={items} />
        <Group items={items} />
      </div>
    </div>
  );
}
