"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// ─── Tab definitions ──────────────────────────────────────────────────────────

type TabId = "options" | "stocks" | "action-plans";

const TABS: { id: TabId; label: string }[] = [
  { id: "options", label: "Options" },
  { id: "stocks", label: "Stocks" },
  { id: "action-plans", label: "Action Plans" },
];

/** Map URL hash → tab id. Only hashes that exactly match are resolved. */
const HASH_TO_TAB: Record<string, TabId> = {
  "#options": "options",
  "#stocks": "stocks",
  "#action-plans": "action-plans",
};

// ─── Component ────────────────────────────────────────────────────────────────

interface Props {
  /** Pre-built server JSX for the Options tab panel. */
  optionsPanel: React.ReactNode;
  /** Pre-built server JSX for the Stocks tab panel. */
  stocksPanel: React.ReactNode;
  /** Pre-built server JSX for the Action Plans tab panel. */
  plansPanel: React.ReactNode;
}

/**
 * Three-tab navigation for the Symbol Detail page.
 *
 * Visual style: pill-style tab bar matching the Options Screener
 * Covered Calls / Cash-Secured Puts switcher.
 *
 * Accessibility: full tablist/tab/tabpanel ARIA pattern with
 * aria-selected, aria-controls, and keyboard navigation
 * (ArrowLeft / ArrowRight / Home / End).
 *
 * Hash behavior:
 * - On mount, reads window.location.hash and resolves to a tab
 *   if the hash matches #options, #stocks, or #action-plans.
 * - On tab change, replaces the current history entry with the
 *   matching hash so bookmarks and browser back/forward work.
 */
export default function SymbolDetailTabs({
  optionsPanel,
  stocksPanel,
  plansPanel,
}: Props) {
  const [activeTab, setActiveTab] = useState<TabId>("options");
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // Resolve initial tab from URL hash on client mount (SSR-safe — hash is
  // unavailable server-side, so we initialize to "options" and correct here).
  useEffect(() => {
    const resolved = HASH_TO_TAB[window.location.hash];
    if (resolved && resolved !== "options") setActiveTab(resolved);
  }, []);

  const handleTabChange = useCallback((id: TabId) => {
    setActiveTab(id);
    // Sync hash without triggering a scroll or a navigation entry push.
    const url = new URL(window.location.href);
    url.hash = id;
    window.history.replaceState(null, "", url.toString());
  }, []);

  // ARIA keyboard pattern: ArrowLeft/Right cycle tabs; Home/End jump to ends.
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent, idx: number) => {
      let nextIdx: number | null = null;
      if (e.key === "ArrowRight") nextIdx = (idx + 1) % TABS.length;
      else if (e.key === "ArrowLeft") nextIdx = (idx - 1 + TABS.length) % TABS.length;
      else if (e.key === "Home") nextIdx = 0;
      else if (e.key === "End") nextIdx = TABS.length - 1;

      if (nextIdx !== null) {
        e.preventDefault();
        tabRefs.current[nextIdx]?.focus();
        handleTabChange(TABS[nextIdx].id);
      }
    },
    [handleTabChange],
  );

  const panels: Record<TabId, React.ReactNode> = {
    options: optionsPanel,
    stocks: stocksPanel,
    "action-plans": plansPanel,
  };

  return (
    <div className="space-y-4">
      {/* ── Tab bar — pill style matching Options Screener ── */}
      <div
        role="tablist"
        aria-label="Symbol detail sections"
        className="flex items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-card p-1 w-fit"
      >
        {TABS.map((tab, idx) => (
          <button
            key={tab.id}
            ref={(el) => { tabRefs.current[idx] = el; }}
            role="tab"
            id={`sym-tab-${tab.id}`}
            aria-selected={activeTab === tab.id}
            aria-controls={`sym-panel-${tab.id}`}
            tabIndex={activeTab === tab.id ? 0 : -1}
            type="button"
            onClick={() => handleTabChange(tab.id)}
            onKeyDown={(e) => handleKeyDown(e, idx)}
            className={`rounded-[var(--radius-pill)] px-4 py-1.5 text-sm transition ${
              activeTab === tab.id
                ? "bg-accent-blue text-white"
                : "text-text-muted hover:text-text"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Tab panels — conditionally rendered so client components only
           mount (and fetch) when their tab is first activated. ── */}
      {TABS.map((tab) =>
        activeTab === tab.id ? (
          <div
            key={tab.id}
            role="tabpanel"
            id={`sym-panel-${tab.id}`}
            aria-labelledby={`sym-tab-${tab.id}`}
          >
            {panels[tab.id]}
          </div>
        ) : null,
      )}
    </div>
  );
}
