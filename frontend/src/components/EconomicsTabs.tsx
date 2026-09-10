"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// Matches the Calls/Puts tab styling used in OptionsScreenerView.tsx.
function navClass(active: boolean) {
  return `rounded-[var(--radius-pill)] px-4 py-1.5 text-sm transition no-underline ${
    active ? "bg-accent-blue text-white!" : "text-text-muted! hover:text-text!"
  }`;
}

const TABS = [
  { href: "/economics", label: "Overview", keys: ["year", "month", "symbol", "source", "account_id"] },
  { href: "/economics/options", label: "Options", keys: ["year", "month", "symbol", "type", "status", "account_id"] },
  { href: "/economics/dividends", label: "Dividends", keys: ["year", "month", "symbol", "account_id"] },
] as const;

function buildHref(
  href: string,
  keys: readonly string[],
  searchParams: URLSearchParams,
) {
  const params = new URLSearchParams();
  for (const key of keys) {
    const value = searchParams.get(key);
    if (value) params.set(key, value);
  }
  const qs = params.toString();
  return qs ? `${href}?${qs}` : href;
}

export default function EconomicsTabs() {
  const pathname = usePathname();
  const searchParams = new URLSearchParams(
    typeof window === "undefined" ? "" : window.location.search,
  );

  return (
    <div className="flex items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-card p-1">
      {TABS.map((tab) => (
        <Link
          key={tab.href}
          href={buildHref(tab.href, tab.keys, searchParams)}
          className={navClass(pathname === tab.href)}
        >
          {tab.label}
        </Link>
      ))}
    </div>
  );
}
