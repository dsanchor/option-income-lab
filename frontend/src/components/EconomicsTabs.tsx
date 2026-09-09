"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const linkBase =
  "inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] px-3 py-1.5 text-sm transition-all no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/60";

function navClass(active: boolean) {
  return `${linkBase} ${
    active ? "bg-bg-hover text-text!" : "text-text-muted! hover:bg-bg-hover hover:text-text!"
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
    <div className="w-fit rounded-[var(--radius-pill)] border border-border bg-bg-card p-1">
      <div className="flex flex-wrap items-center gap-2">
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
    </div>
  );
}
