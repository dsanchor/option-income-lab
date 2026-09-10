"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

// Matches the Calls/Puts tab styling used in OptionsScreenerView.tsx.
function navClass(active: boolean) {
  return `rounded-[var(--radius-pill)] px-4 py-1.5 text-sm transition no-underline ${
    active ? "bg-accent-blue text-white!" : "text-text-muted! hover:text-text!"
  }`;
}

const TABS = [
  { href: "/economics", label: "Overview", keys: ["year", "month", "symbol", "source"] },
  { href: "/economics/options", label: "Options", keys: ["year", "month", "symbol", "type", "status"] },
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
  // Client-only search string, read after mount (matches the pattern used in
  // EconomicsView/DividendsView) to avoid next/navigation's useSearchParams(),
  // which requires a <Suspense> boundary and otherwise breaks static prerendering.
  const [search, setSearch] = useState("");

  useEffect(() => {
    setSearch(window.location.search);
  }, [pathname]);

  const searchParams = new URLSearchParams(search);

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
