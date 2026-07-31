"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, useRef, useEffect, useCallback } from "react";
import {
  FlaskConical,
  LayoutDashboard,
  Banknote,
  MessageSquare,
  Search,
  LineChart,
  CalendarDays,
  ClipboardList,
  Settings,
  ScrollText,
  Bug,
  Menu,
  X,
  ChevronDown,
  type LucideIcon,
} from "lucide-react";
import type { SymbolsOverview } from "@/types/symbols";

type Item = { href: string; label: string; icon: LucideIcon };

const DROPDOWNS: Record<string, Item[]> = {
  Symbols: [
    { href: "/symbols", label: "Watchlist", icon: LineChart },
    { href: "/symbols/calendar", label: "Calendar", icon: CalendarDays },
    { href: "/plans", label: "Action Plans", icon: ClipboardList },
  ],
  Settings: [
    { href: "/settings/config", label: "Configuration", icon: Settings },
    { href: "/settings/logs", label: "Agent Logs", icon: ScrollText },
    { href: "/settings/debug", label: "Debug", icon: Bug },
  ],
};

function isActive(pathname: string, href: string, exact = false) {
  if (exact) return pathname === href;
  return pathname === href || pathname.startsWith(href + "/");
}

const linkBase =
  "inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] px-3 py-1.5 transition-all no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/60";
// `!` importance is required so nav links beat the un-layered global `a { color }`
// rule (otherwise anchors render accent-blue while dropdown buttons render muted).
function navClass(active: boolean) {
  return `${linkBase} ${
    active ? "bg-bg-hover text-text!" : "text-text-muted! hover:bg-bg-hover hover:text-text!"
  }`;
}

export function TopNav() {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);

  const dashboardActive = pathname === "/" || isActive(pathname, "/dashboard");
  const symbolsActive =
    isActive(pathname, "/symbols") || isActive(pathname, "/plans");
  const settingsActive = isActive(pathname, "/settings");

  // Close the mobile panel on route change (state-adjust-during-render pattern).
  const [prevPath, setPrevPath] = useState(pathname);
  if (prevPath !== pathname) {
    setPrevPath(pathname);
    setMobileOpen(false);
  }

  return (
    <nav className="sticky top-0 z-[100] border-b border-border/70 bg-bg-card/80 shadow-[0_1px_0_rgba(255,255,255,0.03)] backdrop-blur-xl">
      <div className="flex flex-wrap items-center gap-x-8 gap-y-2 px-6 py-3">
        <Link
          href="/dashboard"
          className="group flex items-center gap-2 whitespace-nowrap text-[1.1rem] font-semibold no-underline"
        >
          <span className="grid h-8 w-8 place-items-center rounded-[10px] bg-[image:var(--grad-blue)] text-base text-white shadow-[var(--shadow-glow-blue)] transition-transform group-hover:scale-105">
            <FlaskConical size={17} />
          </span>
          <span className="text-gradient">Option Income Lab</span>
        </Link>

        {/* Desktop nav */}
        <div className="hidden flex-wrap items-center gap-2 md:flex">
          <Link href="/dashboard" className={navClass(dashboardActive)}>
            <LayoutDashboard size={16} className="shrink-0" /> Dashboard
          </Link>

          <Dropdown label="Symbols" items={DROPDOWNS.Symbols} active={symbolsActive} pathname={pathname} />

          <Link href="/economics" className={navClass(isActive(pathname, "/economics"))}>
            <Banknote size={16} className="shrink-0" /> Economics
          </Link>
          <Link href="/chat" className={navClass(isActive(pathname, "/chat", true))}>
            <MessageSquare size={16} className="shrink-0" /> Chat
          </Link>
          <Link href="/dgi" className={navClass(isActive(pathname, "/dgi"))}>
            <Search size={16} className="shrink-0" /> DGI Screener
          </Link>

          <Dropdown label="Settings" items={DROPDOWNS.Settings} active={settingsActive} pathname={pathname} />

          <SymbolSearch />
        </div>

        {/* Mobile toggle */}
        <button
          type="button"
          onClick={() => setMobileOpen((v) => !v)}
          aria-label="Toggle navigation menu"
          aria-expanded={mobileOpen}
          aria-controls="mobile-nav-panel"
          className="ml-auto grid h-9 w-9 place-items-center rounded-[var(--radius-pill)] border border-border text-text-muted transition-colors hover:bg-bg-hover hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/60 md:hidden"
        >
          {mobileOpen ? <X size={18} /> : <Menu size={18} />}
        </button>
      </div>

      {/* Mobile panel */}
      {mobileOpen && (
        <div
          id="mobile-nav-panel"
          className="border-t border-border/70 px-4 pb-4 pt-2 md:hidden"
        >
          <div className="mb-3">
            <SymbolSearch mobile />
          </div>
          <div className="flex flex-col gap-1">
            <MobileLink href="/dashboard" label="Dashboard" icon={LayoutDashboard} active={dashboardActive} />
            <MobileSection label="Symbols" items={DROPDOWNS.Symbols} pathname={pathname} />
            <MobileLink href="/economics" label="Economics" icon={Banknote} active={isActive(pathname, "/economics")} />
            <MobileLink href="/chat" label="Chat" icon={MessageSquare} active={isActive(pathname, "/chat", true)} />
            <MobileLink href="/dgi" label="DGI Screener" icon={Search} active={isActive(pathname, "/dgi")} />
            <MobileSection label="Settings" items={DROPDOWNS.Settings} pathname={pathname} />
          </div>
        </div>
      )}
    </nav>
  );
}

function MobileLink({ href, label, icon: Icon, active }: { href: string; label: string; icon: LucideIcon; active: boolean }) {
  return (
    <Link
      href={href}
      className={`flex items-center gap-2 rounded-[var(--radius)] px-3 py-2 text-sm no-underline transition-colors ${
        active ? "bg-bg-hover text-text!" : "text-text-muted! hover:bg-bg-hover hover:text-text!"
      }`}
    >
      <Icon size={16} className="shrink-0" /> {label}
    </Link>
  );
}

function MobileSection({ label, items, pathname }: { label: string; items: Item[]; pathname: string }) {
  return (
    <div className="mt-1">
      <div className="px-3 pb-1 pt-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
        {label}
      </div>
      {items.map((it) => {
        const Icon = it.icon;
        return (
          <Link
            key={it.href}
            href={it.href}
            className={`flex items-center gap-2 rounded-[var(--radius)] px-3 py-2 text-sm no-underline transition-colors ${
              isActive(pathname, it.href, it.href === "/symbols")
                ? "bg-bg-hover text-text!"
                : "text-text-muted! hover:bg-bg-hover hover:text-text!"
            }`}
          >
            <Icon size={16} className="shrink-0" /> {it.label}
          </Link>
        );
      })}
    </div>
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
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimer = () => {
    if (closeTimer.current) {
      clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  };
  const openNow = useCallback(() => {
    clearTimer();
    setOpen(true);
  }, []);
  // Hover-intent: small delay before closing so a diagonal mouse move survives.
  const closeSoon = () => {
    clearTimer();
    closeTimer.current = setTimeout(() => setOpen(false), 150);
  };

  // Close on route change (state-adjust-during-render pattern).
  const [prevPath, setPrevPath] = useState(pathname);
  if (prevPath !== pathname) {
    setPrevPath(pathname);
    setOpen(false);
  }

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  useEffect(() => () => clearTimer(), []);

  const focusItem = (idx: number) => {
    const links = menuRef.current?.querySelectorAll<HTMLAnchorElement>("a");
    if (!links || !links.length) return;
    const i = (idx + links.length) % links.length;
    links[i]?.focus();
  };

  const onTriggerKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openNow();
      requestAnimationFrame(() => focusItem(0));
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  const onMenuKeyDown = (e: React.KeyboardEvent) => {
    const links = Array.from(menuRef.current?.querySelectorAll<HTMLAnchorElement>("a") ?? []);
    const cur = links.indexOf(document.activeElement as HTMLAnchorElement);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      focusItem(cur + 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      focusItem(cur - 1);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      btnRef.current?.focus();
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  };

  return (
    <div ref={wrapRef} className="relative" onMouseEnter={openNow} onMouseLeave={closeSoon}>
      <button
        ref={btnRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        onKeyDown={onTriggerKeyDown}
        aria-haspopup="menu"
        aria-expanded={open}
        className={`${navClass(active)} inline-flex items-center gap-1 select-none`}
      >
        {label}
        <ChevronDown size={13} className={`transition-transform ${open ? "rotate-180" : ""}`} aria-hidden />
      </button>
      <div
        ref={menuRef}
        role="menu"
        aria-label={label}
        onKeyDown={onMenuKeyDown}
        className={`absolute left-0 top-full z-[110] mt-1 min-w-[190px] rounded-[var(--radius)] border border-border bg-bg-card py-1 shadow-lg transition-opacity ${
          open ? "visible opacity-100" : "invisible opacity-0"
        }`}
      >
        {items.map((it) => {
          const Icon = it.icon;
          return (
            <Link
              key={it.href}
              href={it.href}
              role="menuitem"
              tabIndex={open ? 0 : -1}
              className={`flex items-center gap-2 px-4 py-2 text-sm no-underline transition-colors hover:bg-bg-hover focus-visible:bg-bg-hover focus-visible:outline-none ${
                isActive(pathname, it.href, it.href === "/symbols") ? "text-text" : "text-text-muted"
              } hover:text-text`}
            >
              <Icon size={15} className="shrink-0 opacity-80" /> {it.label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}

type Suggestion = { symbol: string; display_name: string };

function SymbolSearch({ mobile = false }: { mobile?: boolean }) {
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
    <div ref={boxRef} className={mobile ? "relative w-full" : "relative ml-1"}>
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
        className={`${
          mobile ? "w-full" : "w-[220px]"
        } rounded-[var(--radius-pill)] border border-border bg-bg-input px-3.5 py-1.5 text-[0.9rem] text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none`}
      />
      {open && (query ? matches.length > 0 : false) && (
        <ul
          id="symbol-search-list"
          role="listbox"
          className={`absolute z-[120] mt-2 max-h-80 overflow-auto rounded-[var(--radius)] border border-border bg-bg-card py-1 shadow-lg ${
            mobile ? "left-0 right-0 w-full" : "right-0 w-[260px]"
          }`}
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
      {open && query && matches.length === 0 && all !== null && (
        <div
          className={`absolute z-[120] mt-2 rounded-[var(--radius)] border border-border bg-bg-card px-4 py-2 text-sm text-text-muted shadow-lg ${
            mobile ? "left-0 right-0" : "right-0 w-[260px]"
          }`}
        >
          No matches. Press Enter to open “{query}”.
        </div>
      )}
    </div>
  );
}
