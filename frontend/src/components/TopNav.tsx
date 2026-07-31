"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, useRef, useEffect } from "react";
import type { SymbolsOverview } from "@/types/symbols";

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
  "rounded-[var(--radius-pill)] px-3 py-1.5 text-text-muted transition-all hover:bg-bg-hover hover:text-text no-underline";
const linkActive = "text-text";

export function TopNav() {
  const pathname = usePathname();

  const dashboardActive = pathname === "/" || isActive(pathname, "/dashboard");
  const symbolsActive =
    isActive(pathname, "/symbols") || isActive(pathname, "/plans");
  const settingsActive = isActive(pathname, "/settings");

  return (
    <nav className="sticky top-0 z-[100] flex flex-wrap items-center gap-x-8 gap-y-2 border-b border-border/70 bg-bg-card/80 px-6 py-3 shadow-[0_1px_0_rgba(255,255,255,0.03)] backdrop-blur-xl">
      <Link href="/dashboard" className="group flex items-center gap-2 whitespace-nowrap text-[1.1rem] font-semibold no-underline">
        <span className="grid h-8 w-8 place-items-center rounded-[10px] bg-[image:var(--grad-blue)] text-base shadow-[var(--shadow-glow-blue)] transition-transform group-hover:scale-105">🧪</span>
        <span className="text-gradient">Option Income Lab</span>
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

        <SymbolSearch />
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

type Suggestion = { symbol: string; display_name: string };

function SymbolSearch() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [all, setAll] = useState<Suggestion[] | null>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);

  // Lazily load the tracked-symbol list once (on first focus).
  async function ensureLoaded() {
    if (all !== null) return;
    try {
      const res = await fetch("/api/symbols/overview");
      const data: SymbolsOverview = await res.json();
      setAll(
        (data.rows ?? []).map((r) => ({
          symbol: r.symbol,
          display_name: r.display_name ?? "",
        })),
      );
    } catch {
      setAll([]);
    }
  }

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const query = q.trim().toUpperCase();
  const matches: Suggestion[] = query
    ? (all ?? [])
        .filter(
          (s) =>
            s.symbol.toUpperCase().includes(query) ||
            s.display_name.toUpperCase().includes(query),
        )
        .sort((a, b) => {
          const ap = a.symbol.toUpperCase().startsWith(query) ? 0 : 1;
          const bp = b.symbol.toUpperCase().startsWith(query) ? 0 : 1;
          return ap - bp || a.symbol.localeCompare(b.symbol);
        })
        .slice(0, 8)
    : [];

  function go(sym: string) {
    const s = sym.trim().toUpperCase();
    if (!s) return;
    setOpen(false);
    setQ("");
    router.push(`/symbols/${encodeURIComponent(s)}`);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive((i) => Math.min(i + 1, Math.max(matches.length - 1, 0)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      go(matches[active]?.symbol ?? query);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div ref={boxRef} className="relative ml-1">
      <input
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
          setActive(0);
        }}
        onFocus={() => {
          ensureLoaded();
          if (q) setOpen(true);
        }}
        onKeyDown={onKeyDown}
        placeholder="🔍 Search symbol…"
        autoComplete="off"
        spellCheck={false}
        aria-label="Search symbol"
        role="combobox"
        aria-expanded={open && matches.length > 0}
        aria-controls="symbol-search-list"
        className="w-[220px] rounded-[var(--radius-pill)] border border-border bg-bg-input px-3.5 py-1.5 text-[0.9rem] text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none"
      />
      {open && matches.length > 0 && (
        <ul
          id="symbol-search-list"
          role="listbox"
          className="absolute right-0 z-[120] mt-2 max-h-80 w-[260px] overflow-auto rounded-[var(--radius)] border border-border bg-bg-card py-1 shadow-lg"
        >
          {matches.map((m, i) => (
            <li key={m.symbol} role="option" aria-selected={i === active}>
              <button
                type="button"
                onMouseEnter={() => setActive(i)}
                onClick={() => go(m.symbol)}
                className={`flex w-full items-center justify-between gap-3 px-4 py-2 text-left text-sm ${
                  i === active ? "bg-bg-hover" : ""
                }`}
              >
                <span className="font-mono font-semibold text-text">{m.symbol}</span>
                {m.display_name && (
                  <span className="truncate text-xs text-text-muted">{m.display_name}</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
