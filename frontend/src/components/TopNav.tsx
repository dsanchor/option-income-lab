"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";

type Item = { href: string; label: string };

const DROPDOWNS: Record<string, Item[]> = {
  Symbols: [
    { href: "/symbols", label: "📊 Watchlist" },
    { href: "/symbols/calendar", label: "📅 Calendar" },
    { href: "/plans", label: "📋 Action Plans" },
  ],
  Settings: [
    { href: "/settings/config", label: "⚙️ Configuration" },
    { href: "/settings/runtime", label: "📊 Runtime Stats" },
    { href: "/settings/logs", label: "🧾 Agent Logs" },
    { href: "/settings/debug", label: "🔍 Debug" },
  ],
};

function isActive(pathname: string, href: string, exact = false) {
  if (exact) return pathname === href;
  return pathname === href || pathname.startsWith(href + "/");
}

const linkBase =
  "rounded-[var(--radius-pill)] px-3 py-1.5 text-text-muted transition-colors hover:bg-bg-hover hover:text-text no-underline";
const linkActive = "bg-bg-hover text-text";

export function TopNav() {
  const pathname = usePathname();
  const router = useRouter();
  const [q, setQ] = useState("");

  const dashboardActive = pathname === "/" || isActive(pathname, "/dashboard");
  const symbolsActive =
    isActive(pathname, "/symbols") || isActive(pathname, "/plans");
  const settingsActive = isActive(pathname, "/settings");

  function onSearch(e: React.FormEvent) {
    e.preventDefault();
    const sym = q.trim().toUpperCase();
    if (sym) router.push(`/symbols/${encodeURIComponent(sym)}`);
  }

  return (
    <nav className="sticky top-0 z-[100] flex flex-wrap items-center gap-x-8 gap-y-2 border-b border-border bg-bg-card px-6 py-3">
      <Link href="/dashboard" className="whitespace-nowrap text-[1.1rem] font-medium text-text no-underline">
        🧪 Option Income Lab
      </Link>

      <div className="flex flex-wrap items-center gap-2">
        <Link href="/dashboard" className={`${linkBase} ${dashboardActive ? linkActive : ""}`}>
          Dashboard
        </Link>

        <Dropdown label="Symbols" items={DROPDOWNS.Symbols} active={symbolsActive} pathname={pathname} />

        <Link href="/economics" className={`${linkBase} ${isActive(pathname, "/economics") ? linkActive : ""}`}>
          Economics
        </Link>
        <Link href="/chat" className={`${linkBase} ${isActive(pathname, "/chat", true) ? linkActive : ""}`}>
          Chat
        </Link>
        <Link href="/dgi" className={`${linkBase} ${isActive(pathname, "/dgi") ? linkActive : ""}`}>
          DGI Screener
        </Link>

        <Dropdown label="Settings" items={DROPDOWNS.Settings} active={settingsActive} pathname={pathname} />

        <form onSubmit={onSearch} className="ml-1">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="🔍 Search symbol…"
            autoComplete="off"
            spellCheck={false}
            aria-label="Search symbol"
            className="w-[220px] rounded-[var(--radius-pill)] border border-border bg-bg-input px-3.5 py-1.5 text-[0.9rem] text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none"
          />
        </form>
      </div>
    </nav>
  );
}

function Dropdown({
  label,
  items,
  active,
  pathname,
}: {
  label: string;
  items: Item[];
  active: boolean;
  pathname: string;
}) {
  return (
    <div className="group relative">
      <span
        className={`${linkBase} inline-block cursor-default select-none ${active ? linkActive : ""}`}
      >
        {label}
      </span>
      <div className="invisible absolute left-0 top-full z-[110] min-w-[190px] rounded-[var(--radius)] border border-border bg-bg-card py-1 opacity-0 shadow-lg transition-opacity group-hover:visible group-hover:opacity-100">
        {items.map((it) => (
          <Link
            key={it.href}
            href={it.href}
            className={`block px-4 py-2 text-sm no-underline transition-colors hover:bg-bg-hover ${
              isActive(pathname, it.href, it.href === "/symbols") ? "text-text" : "text-text-muted"
            } hover:text-text`}
          >
            {it.label}
          </Link>
        ))}
      </div>
    </div>
  );
}
