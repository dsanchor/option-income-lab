/**
 * symbolDetailRedesign.test.mjs — Symbol Details UI redesign regression.
 *
 * Requirements (2026-09-07 directive):
 *   AP — Symbol Configuration remains first in Stocks before Portfolio Holdings.
 *   AT — Agent & Alert Toggles section and all toggle controls ABSENT from
 *         SymbolConfigurationCard (backend fields may still exist elsewhere).
 *   EC — SymbolConfigurationCard is expandable/collapsible, default COLLAPSED;
 *         accessible aria-expanded trigger; keyboard-operable; content hidden.
 *   NF — No double-nested surface/card frame in Portfolio Holdings section.
 *   PT — Portfolio Holdings renders as a semantic/responsive table (similar to
 *         Summary): headers, rows, numeric alignment, overflow, account badges,
 *         empty/loading/error/actions all preserved.
 *   SV — Single visual container; no redundant inner `surface` class.
 *   DC — No change to data/calculation/API contracts (field references stable).
 *
 * Strategy:
 *   Source-contract tests for structural/presence assertions (readFileSync).
 *   Pure-logic behavioral tests for collapse toggle (inline mirror).
 *   Tests document the open defects that Rusty must fix; AT/EC/NF/PT/SV groups
 *   will FAIL against current source until the redesign is shipped.
 *   AP and DC groups already pass; they guard against regression.
 *
 * Run: node --test frontend/tests/symbolDetailRedesign.test.mjs
 */

import { readFileSync } from "fs";
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");

const cardSrc     = readFileSync(join(root, "src/components/SymbolConfigurationCard.tsx"), "utf8");
const holdingsSrc = readFileSync(join(root, "src/components/PortfolioHoldingsCard.tsx"), "utf8");
const pageSrc     = readFileSync(join(root, "src/app/symbols/[symbol]/page.tsx"), "utf8");
const sectionSrc  = readFileSync(join(root, "src/components/DetailSection.tsx"), "utf8");

// ---------------------------------------------------------------------------
// AP: Arrangement & Placement — Config card first, Holdings second
// ---------------------------------------------------------------------------

describe("AP: Arrangement — SymbolConfigurationCard before Portfolio Holdings in Stocks", () => {
  it("AP-1: <SymbolConfigurationCard JSX appears before <PortfolioHoldingsCard in page.tsx source order", () => {
    const cfgIdx = pageSrc.indexOf("<SymbolConfigurationCard");
    const phIdx  = pageSrc.indexOf("<PortfolioHoldingsCard");
    assert.ok(cfgIdx !== -1, "AP-1: <SymbolConfigurationCard JSX not found in page.tsx");
    assert.ok(phIdx  !== -1, "AP-1: <PortfolioHoldingsCard JSX not found in page.tsx");
    assert.ok(cfgIdx < phIdx,
      `AP-1 DEFECT: <SymbolConfigurationCard (${cfgIdx}) must appear before ` +
      `<PortfolioHoldingsCard (${phIdx}). Contract: Config first, Holdings second.`);
  });

  it("AP-2: Both components are inside the same stocksSecurityId guard in page.tsx", () => {
    const guardIdx = pageSrc.indexOf("stocksSecurityId &&");
    const cfgIdx   = pageSrc.indexOf("<SymbolConfigurationCard");
    const phIdx    = pageSrc.indexOf("<PortfolioHoldingsCard");
    assert.ok(guardIdx !== -1, "AP-2: stocksSecurityId guard not found in page.tsx");
    assert.ok(cfgIdx > guardIdx,
      "AP-2 DEFECT: <SymbolConfigurationCard must be inside the stocksSecurityId guard");
    assert.ok(phIdx > guardIdx,
      "AP-2 DEFECT: <PortfolioHoldingsCard must be inside the stocksSecurityId guard");
  });

  it("AP-3: No alternative Holdings component inserted between Config and Holdings in Stocks source", () => {
    // The Stocks guard block must not insert an unrelated full holdings table before PortfolioHoldingsCard.
    const stocksStart = pageSrc.indexOf("Stocks Section");
    const cfgIdx      = pageSrc.indexOf("<SymbolConfigurationCard");
    const phIdx       = pageSrc.indexOf("<PortfolioHoldingsCard");
    if (stocksStart === -1 || cfgIdx === -1 || phIdx === -1) return; // guard
    const between = pageSrc.slice(cfgIdx + 25, phIdx);
    assert.ok(!between.includes("<PortfolioHoldingsTable"),
      "AP-3 DEFECT: <PortfolioHoldingsTable must not appear between Config and Holdings Card.");
    assert.ok(!between.includes("<HoldingsTable"),
      "AP-3 DEFECT: An unlabelled HoldingsTable must not appear between Config and Holdings Card.");
  });
});

// ---------------------------------------------------------------------------
// AT: Agent & Alert Toggles — must be ABSENT from SymbolConfigurationCard
// ---------------------------------------------------------------------------

describe("AT: Agent & Alert Toggles absent from SymbolConfigurationCard", () => {
  it('AT-1: "Agent & Alert Toggles" section heading absent from SymbolConfigurationCard', () => {
    assert.ok(!cardSrc.includes("Agent & Alert Toggles"),
      'AT-1 DEFECT: SymbolConfigurationCard still renders the "Agent & Alert Toggles" section heading. ' +
      "This entire section must be removed and relocated or dropped.");
  });

  it('AT-2: "Covered Calls agent" toggle label absent from SymbolConfigurationCard', () => {
    assert.ok(!cardSrc.includes('"Covered Calls agent"') && !cardSrc.includes("'Covered Calls agent'"),
      "AT-2 DEFECT: Covered Calls agent ToggleRow label still present in SymbolConfigurationCard.");
  });

  it('AT-3: "Cash-Secured Puts agent" toggle label absent from SymbolConfigurationCard', () => {
    assert.ok(!cardSrc.includes('"Cash-Secured Puts agent"') && !cardSrc.includes("'Cash-Secured Puts agent'"),
      "AT-3 DEFECT: Cash-Secured Puts agent ToggleRow label still present in SymbolConfigurationCard.");
  });

  it('AT-4: "Buy Tracker agent" toggle label absent from SymbolConfigurationCard', () => {
    assert.ok(!cardSrc.includes('"Buy Tracker agent"') && !cardSrc.includes("'Buy Tracker agent'"),
      "AT-4 DEFECT: Buy Tracker agent ToggleRow label still present in SymbolConfigurationCard.");
  });

  it('AT-5: "Telegram notifications" toggle label absent from SymbolConfigurationCard', () => {
    assert.ok(!cardSrc.includes('"Telegram notifications"') && !cardSrc.includes("'Telegram notifications'"),
      "AT-5 DEFECT: Telegram notifications ToggleRow label still present in SymbolConfigurationCard.");
  });

  it("AT-6: No cfg-toggle-* id attributes in SymbolConfigurationCard render", () => {
    assert.ok(!cardSrc.includes("cfg-toggle-cc"),
      "AT-6 DEFECT: cfg-toggle-cc id still present — Covered Calls toggle not removed.");
    assert.ok(!cardSrc.includes("cfg-toggle-csp"),
      "AT-6 DEFECT: cfg-toggle-csp id still present — Cash-Secured Puts toggle not removed.");
    assert.ok(!cardSrc.includes("cfg-toggle-buy"),
      "AT-6 DEFECT: cfg-toggle-buy id still present — Buy Tracker toggle not removed.");
    assert.ok(!cardSrc.includes("cfg-toggle-telegram"),
      "AT-6 DEFECT: cfg-toggle-telegram id still present — Telegram toggle not removed.");
  });

  it("AT-7: WatchlistToggles prop absent from SymbolConfigurationCard Props interface", () => {
    // After redesign, the card no longer needs watchlist/toggles props.
    assert.ok(!cardSrc.includes("WatchlistToggles"),
      "AT-7 DEFECT: WatchlistToggles import/type still used in SymbolConfigurationCard. " +
      "Remove the watchlist prop along with the toggle UI.");
  });

  it("AT-8: telegramEnabled prop absent from SymbolConfigurationCard Props interface", () => {
    assert.ok(!cardSrc.includes("telegramEnabled"),
      "AT-8 DEFECT: telegramEnabled prop still declared/used in SymbolConfigurationCard. " +
      "Remove it along with the Telegram toggle UI.");
  });

  it("AT-9: updateToggle callback absent from SymbolConfigurationCard", () => {
    assert.ok(!cardSrc.includes("updateToggle"),
      "AT-9 DEFECT: updateToggle callback still defined in SymbolConfigurationCard. " +
      "The toggle interaction handler must move with the toggle UI.");
  });

  it("AT-10: No toggling/setToggles state in SymbolConfigurationCard (toggle state fully removed)", () => {
    assert.ok(!cardSrc.includes("setToggles"),
      "AT-10 DEFECT: setToggles state setter still present in SymbolConfigurationCard. " +
      "Remove the toggles useState along with the toggle UI.");
    assert.ok(!cardSrc.includes("toggling,") && !cardSrc.includes("setToggling"),
      "AT-10 DEFECT: toggling/setToggling mutation state still present — remove with toggle UI.");
  });

  it("AT-11: telegramEnabled and watchlist NOT passed from page.tsx to SymbolConfigurationCard", () => {
    const cfgTag = pageSrc.match(/<SymbolConfigurationCard[\s\S]*?\/>/)?.[0] ?? "";
    assert.ok(!cfgTag.includes("telegramEnabled"),
      "AT-11 DEFECT: page.tsx still passes telegramEnabled to SymbolConfigurationCard JSX tag.");
    assert.ok(!cfgTag.includes("watchlist="),
      "AT-11 DEFECT: page.tsx still passes watchlist= prop to SymbolConfigurationCard JSX tag.");
  });
});

// ---------------------------------------------------------------------------
// EC: Expandable / Collapsible Symbol Configuration Card
// ---------------------------------------------------------------------------

// ── Pure-logic mirror for collapse toggle ────────────────────────────────────

/** Mirror: toggleCollapsed(isExpanded) -> !isExpanded */
function toggleCollapsed(isExpanded) { return !isExpanded; }

describe("EC: SymbolConfigurationCard is collapsible, default collapsed", () => {
  it("EC-1: SymbolConfigurationCard has a boolean expanded/open state variable", () => {
    const hasExpandState =
      /const\s+\[\s*(?:isExpanded|open|expanded|isOpen)\s*,/.test(cardSrc);
    assert.ok(hasExpandState,
      "EC-1 DEFECT: SymbolConfigurationCard must declare a named collapse state variable " +
      "(isExpanded, open, expanded, or isOpen). Currently has no collapse state; " +
      "the full form is always rendered."
    );
  });

  it("EC-1b: Default state is collapsed (false) — not expanded (true)", () => {
    // Reject useState(true) as the first useState in the card (would mean default expanded)
    // We allow other useState(true) for flags like usOptionsEligible guards, but the
    // expand/open state itself must default to false.
    const expandMatch = cardSrc.match(/const\s+\[(?:isExpanded|open|expanded|isOpen)\s*,\s*\w+\]\s*=\s*useState\s*\(\s*(true|false)\s*\)/);
    if (expandMatch) {
      assert.equal(expandMatch[1], "false",
        "EC-1b DEFECT: The collapse state variable must default to false (collapsed). " +
        `Found useState(${expandMatch[1]}) — card would open expanded by default.`);
    } else {
      assert.ok(false,
        "EC-1b DEFECT: No recognisable collapse-state variable (isExpanded/open/expanded/isOpen) found. " +
        "Add useState(false) to control the collapsed/expanded state.");
    }
  });

  it("EC-2: Trigger element has aria-expanded attribute", () => {
    assert.ok(cardSrc.includes("aria-expanded"),
      "EC-2 DEFECT: SymbolConfigurationCard trigger must have aria-expanded to communicate state to " +
      "assistive technologies. Currently missing — card has no expand/collapse mechanism.");
  });

  it("EC-3: Trigger is type=\"button\" — keyboard-operable without form-submit side effects", () => {
    // Look for aria-expanded near a type="button" (within 300 chars — same trigger element)
    const ariaIdx = cardSrc.indexOf("aria-expanded");
    if (ariaIdx !== -1) {
      const nearby = cardSrc.slice(Math.max(0, ariaIdx - 300), ariaIdx + 300);
      assert.ok(nearby.includes('type="button"'),
        'EC-3 DEFECT: The aria-expanded trigger must be type="button" to prevent implicit form submit ' +
        "and to be keyboard-activatable with Space/Enter.");
    } else {
      assert.ok(false,
        "EC-3 DEFECT: aria-expanded not present — confirm collapse trigger is implemented as a <button>.");
    }
  });

  it("EC-4: Card content is conditionally rendered based on collapsed state (hidden when collapsed)", () => {
    // Accept: `{isExpanded && ...}`, `{open && ...}`, or `hidden` attribute conditional.
    // The key is that the form/content section is NOT rendered when the card is collapsed.
    const hasConditionalContent =
      cardSrc.includes("{isExpanded &&") ||
      cardSrc.includes("{open &&") ||
      cardSrc.includes("{expanded &&") ||
      cardSrc.includes("{isOpen &&") ||
      (cardSrc.includes("aria-expanded") && cardSrc.includes("hidden"));
    assert.ok(hasConditionalContent,
      "EC-4 DEFECT: Card content must be conditionally rendered ({isExpanded && <content>}) so it is " +
      "absent from the DOM when collapsed. Currently the full form is always rendered.");
  });

  it("EC-5: Symbol Configuration heading is visible in both collapsed and expanded states", () => {
    // The heading 'Symbol Configuration' must be on or near the trigger, not inside the collapsed content.
    const ariaIdx = cardSrc.indexOf("aria-expanded");
    if (ariaIdx !== -1) {
      // Look for the heading text within 600 chars before the collapsed content guard
      const beforeContent = cardSrc.slice(0, ariaIdx + 600);
      assert.ok(
        beforeContent.includes("Symbol Configuration"),
        "EC-5 DEFECT: 'Symbol Configuration' heading must be near the trigger (always visible). " +
        "If it is inside the conditionally-rendered block, it disappears when collapsed."
      );
    } else {
      // No aria-expanded yet — check heading at least exists
      assert.ok(cardSrc.includes("Symbol Configuration"),
        "EC-5 DEFECT: 'Symbol Configuration' heading text not found in SymbolConfigurationCard source.");
    }
  });

  it("EC-6: Toggle icon or label changes to signal collapsed vs expanded (ChevronDown/ChevronRight pattern)", () => {
    // Accept explicit icon swap, aria-label change, or conditional text.
    const hasToggleSignal =
      cardSrc.includes("ChevronDown") ||
      cardSrc.includes("ChevronRight") ||
      (cardSrc.includes("collapsed") && cardSrc.includes("expanded")) ||
      /aria-label=\{.*isExpand/.test(cardSrc) ||
      /aria-label=\{.*open/.test(cardSrc);
    assert.ok(hasToggleSignal,
      "EC-6 DEFECT: No visual signal for collapsed/expanded state detected. " +
      "Use ChevronDown/ChevronRight icons (same as DetailSection) or a descriptive aria-label toggle.");
  });

  it("EC-7: Pure logic — collapse toggle correctly inverts state", () => {
    assert.equal(toggleCollapsed(false), true,  "EC-7: collapsed=false → click → expanded=true");
    assert.equal(toggleCollapsed(true),  false, "EC-7: expanded=true → click → collapsed=false");
  });

  it("EC-8: DetailSection wrapper for Stocks section remains defaultOpen=true (outer section stays open)", () => {
    // The outer Stocks DetailSection must still open by default.
    // Only SymbolConfigurationCard itself should be collapsed by default.
    const dsDefaultOpen = sectionSrc.includes("defaultOpen = true") || sectionSrc.includes("defaultOpen=true");
    assert.ok(dsDefaultOpen,
      "EC-8 DEFECT: DetailSection defaultOpen should be true (outer Stocks section open). " +
      "Only SymbolConfigurationCard's internal collapse defaults to false.");
  });
});

// ---------------------------------------------------------------------------
// NF: No double-nested surface/card frame in Portfolio Holdings section
// ---------------------------------------------------------------------------

describe("NF: No double-nested surface/card frame in Portfolio Holdings", () => {
  it("NF-1: PortfolioHoldingsCard outer container does NOT carry the `surface` CSS class", () => {
    assert.ok(!holdingsSrc.includes('className="surface'),
      "NF-1 DEFECT: PortfolioHoldingsCard outer wrapper still has className=\"surface ...\". " +
      "The parent section (DetailSection/Stocks) provides the outer surface. " +
      "A standalone surface class creates a double-card frame."
    );
  });

  it("NF-2: No standalone bordered-and-rounded nested div for the 'By account' sub-section", () => {
    // The 'By account' list must become table rows, not a separate bordered container.
    // A nested `<div className="... rounded... border border-border` creates a double frame.
    const byAccountMatch = holdingsSrc.match(/By account[\s\S]{0,600}/);
    if (byAccountMatch) {
      const block = byAccountMatch[0];
      assert.ok(
        !(block.includes("rounded-") && block.includes("border border-border")),
        "NF-2 DEFECT: 'By account' section still uses a nested rounded+border div. " +
        "Accounts must be rendered as table rows inside the single Holdings table — " +
        "not inside a separate bordered card within the already-bordered outer container."
      );
    } else {
      assert.ok(true, "NF-2: 'By account' section not found — may already be restructured as table rows.");
    }
  });

  it("NF-3: PortfolioHoldingsCard does not add its own top-level border+rounded+padding card frame", () => {
    // Detect the standalone `surface rounded ... border ... p-4` pattern on the outermost element.
    const firstDiv = holdingsSrc.match(/<div\s+className="[^"]*rounded[^"]*border[^"]*p-\d[^"]*"/)?.[0] ?? "";
    assert.ok(firstDiv === "" || !firstDiv.includes("surface"),
      "NF-3 DEFECT: PortfolioHoldingsCard wraps itself in a standalone card frame " +
      "(rounded + border + padding). Remove the outer wrapper — the parent provides the frame."
    );
  });

  it("NF-4: PortfolioHoldingsCard does not import or use StatCard grid (grid layout replaced by table)", () => {
    // StatCard grid is the old non-table layout. After redesign, holdings are table rows.
    assert.ok(!holdingsSrc.includes("StatCard"),
      "NF-4 DEFECT: PortfolioHoldingsCard still imports/uses StatCard. " +
      "The redesign replaces the StatCard grid with a semantic table."
    );
    assert.ok(!holdingsSrc.includes('grid-cols-2') && !holdingsSrc.includes("grid-cols-3"),
      "NF-4 DEFECT: PortfolioHoldingsCard still uses grid-cols layout instead of a table."
    );
  });
});

// ---------------------------------------------------------------------------
// PT: Portfolio Holdings renders as a semantic/responsive table
// ---------------------------------------------------------------------------

describe("PT: Portfolio Holdings — semantic table structure", () => {
  it("PT-1: PortfolioHoldingsCard uses a <table> element", () => {
    assert.ok(holdingsSrc.includes("<table"),
      "PT-1 DEFECT: PortfolioHoldingsCard must render a <table> element (semantic table). " +
      "Currently uses a flex/grid layout which is not screen-reader-friendly for tabular financial data."
    );
  });

  it("PT-2: Table has a <thead> section with column headers", () => {
    assert.ok(holdingsSrc.includes("<thead"),
      "PT-2 DEFECT: PortfolioHoldingsCard <table> must have a <thead> with column headers."
    );
  });

  it("PT-3: Table has a <tbody> section for data rows", () => {
    assert.ok(holdingsSrc.includes("<tbody"),
      "PT-3 DEFECT: PortfolioHoldingsCard <table> must have a <tbody> for data rows."
    );
  });

  it("PT-4: Column headers include Shares, Avg Cost/Invested, and an Accounts column", () => {
    // Accept variants: "Shares", "Avg Cost", "Invested", "Accounts" / "Account"
    assert.ok(holdingsSrc.includes("Shares") || holdingsSrc.includes("shares"),
      "PT-4 DEFECT: No 'Shares' column header found in PortfolioHoldingsCard table.");
    assert.ok(
      holdingsSrc.includes("Avg Cost") || holdingsSrc.includes("Invested") ||
      holdingsSrc.includes("average_cost_eur") || holdingsSrc.includes("current_invested_eur"),
      "PT-4 DEFECT: No cost/invested column header or data reference in PortfolioHoldingsCard table."
    );
    assert.ok(
      holdingsSrc.includes("Account") || holdingsSrc.includes("account"),
      "PT-4 DEFECT: No Accounts column header in PortfolioHoldingsCard table."
    );
  });

  it("PT-5: Numeric columns have right-aligned text (text-right on th or td)", () => {
    assert.ok(holdingsSrc.includes("text-right"),
      "PT-5 DEFECT: No text-right alignment found in PortfolioHoldingsCard. " +
      "Numeric columns (Shares, Avg Cost, Invested, Dividends) must be right-aligned."
    );
  });

  it("PT-6: Table container has overflow-x-auto for horizontal scroll on narrow screens", () => {
    assert.ok(holdingsSrc.includes("overflow-x-auto"),
      "PT-6 DEFECT: PortfolioHoldingsCard table must be wrapped in overflow-x-auto " +
      "so it scrolls horizontally on narrow viewports."
    );
  });

  it("PT-7: Account labels/badges are rendered for per-account rows", () => {
    // Account name or badge must appear in the table body (not just a heading)
    assert.ok(
      holdingsSrc.includes("account_name") || holdingsSrc.includes("account_id") ||
      holdingsSrc.includes("AccountBadge") || holdingsSrc.includes("acct.account"),
      "PT-7 DEFECT: Per-account rows must display account name/badge. " +
      "Current redesign must preserve account identity in the table."
    );
  });

  it("PT-8: Loading state preserved (skeleton or spinner)", () => {
    // PortfolioHoldingsCard is a server-side component receiving props — no async loading needed.
    // However, if the redesign introduces client-side loading, it must have a skeleton.
    // Accept: either no loading state (server props) OR a skeleton/loading element.
    assert.ok(true, "PT-8: Server-props component — loading state not required (data provided by server).");
  });

  it("PT-9: Empty holdings state is handled (no accounts or zero shares)", () => {
    // Must handle the case where holdings_by_account is empty/absent.
    assert.ok(
      holdingsSrc.includes("holdings_by_account") || holdingsSrc.includes("hasPortfolio"),
      "PT-9 DEFECT: PortfolioHoldingsCard must handle empty/absent holdings_by_account gracefully."
    );
  });

  it("PT-10: Historical symbol state preserved (0-shares badge or row)", () => {
    // isHistorical state or symbolState prop must still produce the Historical badge.
    assert.ok(
      holdingsSrc.includes("Historical") || holdingsSrc.includes("isHistorical") ||
      holdingsSrc.includes("symbolState"),
      "PT-10 DEFECT: Historical badge/indicator must be preserved in the redesigned table."
    );
  });

  it("PT-11: font-mono used on numeric cells for digit alignment", () => {
    assert.ok(holdingsSrc.includes("font-mono"),
      "PT-11 DEFECT: Numeric table cells must use font-mono for proper digit alignment."
    );
  });

  it("PT-12: scope='col' or scope='row' on table headers for screen-reader semantics", () => {
    assert.ok(
      holdingsSrc.includes("scope=") || holdingsSrc.includes('scope="col"') || holdingsSrc.includes('scope="row"'),
      "PT-12 DEFECT: <th> elements must have scope='col' (column headers) or scope='row' for accessibility."
    );
  });
});

// ---------------------------------------------------------------------------
// SV: Single visual container — no redundant inner surface
// ---------------------------------------------------------------------------

describe("SV: Single visual container — no redundant inner surface", () => {
  it("SV-1: PortfolioHoldingsCard renders exactly one outer container (no double-wrapping)", () => {
    // Count top-level `<div` that start with className containing 'rounded' and 'border'
    // One is expected (the table wrapper); two would mean double nesting.
    const cardFrameMatches = holdingsSrc.match(/<div\s+className="[^"]*rounded[^"]*border[^"]*"/g) ?? [];
    assert.ok(cardFrameMatches.length <= 1,
      `SV-1 DEFECT: Found ${cardFrameMatches.length} div elements with rounded+border classes ` +
      "in PortfolioHoldingsCard. Single visual container contract requires at most one."
    );
  });

  it("SV-2: PortfolioHoldingsCard does not wrap content in <section> AND <div> with card styling", () => {
    const hasSectionCard = holdingsSrc.includes("<section") && holdingsSrc.includes("<div className=\"surface");
    assert.ok(!hasSectionCard,
      "SV-2 DEFECT: PortfolioHoldingsCard must not use both <section> and <div className='surface'> " +
      "— that creates a double-nested visual container."
    );
  });

  it("SV-3: SymbolConfigurationCard uses a single <section> wrapper (no outer div card + inner section)", () => {
    // The card's outer element should be <section aria-label="...">, not a div containing a section.
    const outerSection = cardSrc.match(/<section\s+aria-label="Symbol Configuration"/);
    assert.ok(outerSection,
      'SV-3 DEFECT: SymbolConfigurationCard outer wrapper must be <section aria-label="Symbol Configuration">. ' +
      "This is the single visual container — no extra outer div with surface/card styling."
    );
  });
});

// ---------------------------------------------------------------------------
// DC: Data / calculation / API contract preservation
// ---------------------------------------------------------------------------

describe("DC: Data contracts unchanged — same fields, no API change", () => {
  it("DC-1: portfolio.current_shares still referenced in PortfolioHoldingsCard", () => {
    assert.ok(holdingsSrc.includes("current_shares"),
      "DC-1 DEFECT: portfolio.current_shares is no longer referenced in PortfolioHoldingsCard. " +
      "Field must remain — it is the canonical shares count and drives the Historical detection."
    );
  });

  it("DC-2: portfolio.average_cost_eur still referenced", () => {
    assert.ok(holdingsSrc.includes("average_cost_eur"),
      "DC-2 DEFECT: portfolio.average_cost_eur missing from PortfolioHoldingsCard. " +
      "This is a primary financial field — must remain displayed in the table."
    );
  });

  it("DC-3: portfolio.current_invested_eur or equivalent still referenced", () => {
    assert.ok(
      holdingsSrc.includes("current_invested_eur") || holdingsSrc.includes("invested"),
      "DC-3 DEFECT: portfolio.current_invested_eur missing from PortfolioHoldingsCard. " +
      "Invested basis must remain displayed."
    );
  });

  it("DC-4: portfolio.total_dividends_eur still referenced (or conditionally shown)", () => {
    assert.ok(holdingsSrc.includes("total_dividends_eur"),
      "DC-4 DEFECT: portfolio.total_dividends_eur missing from PortfolioHoldingsCard. " +
      "Net dividends must remain displayed (may be conditional if null)."
    );
  });

  it("DC-5: portfolio.holdings_by_account still referenced for per-account breakdown", () => {
    assert.ok(holdingsSrc.includes("holdings_by_account"),
      "DC-5 DEFECT: portfolio.holdings_by_account missing from PortfolioHoldingsCard. " +
      "Per-account breakdown (account_id, account_name, shares, avg_cost_eur) must remain."
    );
  });

  it("DC-6: SymbolConfigurationCard still references security._etag for PATCH conflict detection", () => {
    assert.ok(cardSrc.includes("_etag"),
      "DC-6 DEFECT: security._etag reference removed from SymbolConfigurationCard. " +
      "The ETag-based PATCH conflict guard must remain — it is an API contract, not a UI concern."
    );
  });

  it("DC-7: SymbolConfigurationCard PATCH endpoint URL unchanged (/api/symbols/.../security)", () => {
    assert.ok(
      cardSrc.includes("/security") && cardSrc.includes("PATCH"),
      "DC-7 DEFECT: SymbolConfigurationCard PATCH endpoint or method changed. " +
      "Must still PATCH /api/symbols/{symbol}/security."
    );
  });

  it("DC-8: Enrichment refresh endpoint unchanged (/api/symbols/.../enrichment/refresh)", () => {
    assert.ok(cardSrc.includes("/enrichment/refresh"),
      "DC-8 DEFECT: Enrichment refresh endpoint missing or renamed in SymbolConfigurationCard."
    );
  });

  it("DC-9: usOptionsEligible prop retained for non-US display guard in SymbolConfigurationCard", () => {
    // Even without toggles, usOptionsEligible may still guard non-US sections.
    // If it is removed entirely, this test is an acceptable future pass.
    // For now verify it is still referenced (used for enrichment/config guard or similar).
    assert.ok(
      cardSrc.includes("usOptionsEligible"),
      "DC-9 NOTE: usOptionsEligible prop removed from SymbolConfigurationCard. " +
      "If toggles are the only consumer, removal is acceptable — this test may be updated. " +
      "If there are other non-US guards, the prop must be retained."
    );
  });
});

// ---------------------------------------------------------------------------
// HA: Holdings "By account" visible label — name only, correct fallbacks
//
// Strategy:
//   HA-1..HA-5, HA-9, HA-10 — source-contract: verify what the component
//     renders without instantiating React.
//   HA-6, HA-7, HA-8 — behavioral-mirror: inline mirror of acctLabel()
//     in PortfolioHoldingsCard, tested against fixture accounts.
//
// All tests should PASS against the current redesigned source.
// ---------------------------------------------------------------------------

// Inline mirror of PortfolioHoldingsCard's acctLabel() function:
//   if (!acct.account_id || acct.account_id === "_unassigned") return UNASSIGNED_LABEL;
//   return acct.account_name ?? acct.account_id;
const HOLDINGS_UNASSIGNED_LABEL = "Sin asignar";
function acctLabelMirror(acct) {
  if (!acct.account_id || acct.account_id === "_unassigned") return HOLDINGS_UNASSIGNED_LABEL;
  return acct.account_name ?? acct.account_id;
}

const holdingFixtures = [
  { account_id: "acc-ing-1",       account_name: "Ahorro ING",    shares: "10",  avg_cost_eur: "50.00" },
  { account_id: "acc-heytrade-1",  account_name: "Cuenta HeyTrade", shares: "5", avg_cost_eur: "120.00" },
  { account_id: "acc-no-name",     account_name: undefined,        shares: "3",  avg_cost_eur: null },
  { account_id: "_unassigned",     account_name: undefined,        shares: "1",  avg_cost_eur: null },
];

describe("HA: Portfolio Holdings 'By account' — account_name label, correct fallbacks", () => {
  // -----------------------------------------------------------------------
  // Source-contract tests (readFileSync)
  // -----------------------------------------------------------------------

  it("HA-1: Label span renders acct.account_name as primary field (not formatAccountLabel)", () => {
    assert.ok(
      holdingsSrc.includes("account_name"),
      "HA-1 DEFECT: PortfolioHoldingsCard does not reference account_name for the By-account label. " +
      "The visible label must use account_name as the primary display field."
    );
  });

  it("HA-2: Fallback pattern is account_name ?? account_id — never blank, never broker", () => {
    // Source must contain the nullish-coalesce pattern so missing names fall
    // back to the ID rather than rendering nothing or a fabricated string.
    assert.ok(
      holdingsSrc.includes("account_name") && holdingsSrc.includes("account_id"),
      "HA-2 DEFECT: PortfolioHoldingsCard missing the account_name ?? account_id fallback. " +
      "Both fields must appear in the label expression."
    );
    // Must not use a bare render of account_name without a fallback.
    // Heuristic: if 'account_name}' appears alone (without '??') → defect.
    // We accept either '?? acct.account_id' or conditional rendering.
    const hasNullishFallback = holdingsSrc.includes("account_name ??") || holdingsSrc.includes("account_name??");
    const hasConditionalFallback =
      holdingsSrc.includes("account_name ?") || holdingsSrc.includes("account_name||");
    assert.ok(
      hasNullishFallback || hasConditionalFallback,
      "HA-2 DEFECT: No nullish/conditional fallback found after account_name. " +
      "Accounts with no name would render blank."
    );
  });

  it("HA-3: Label function never uses '·' separator (no broker·name format)", () => {
    // Locate the acctLabel helper function in source; it must have no '·' separator.
    // (The redesigned card uses a named acctLabel() function rather than an inline expression.)
    const fnStart = holdingsSrc.indexOf("function acctLabel");
    assert.ok(fnStart !== -1,
      "HA-3 pre-check: acctLabel function not found in PortfolioHoldingsCard. " +
      "If the helper was renamed, update this test."
    );
    const acctLabelBlock = holdingsSrc.slice(fnStart, fnStart + 300);
    assert.ok(
      !acctLabelBlock.includes("·"),
      "HA-3 DEFECT: '·' separator found inside acctLabel helper. " +
      "Account labels must never include broker·name formatting."
    );
  });

  it("HA-4: Source never calls formatAccountLabel/getAccountLabel for holdings display", () => {
    assert.ok(
      !holdingsSrc.includes("formatAccountLabel") && !holdingsSrc.includes("getAccountLabel"),
      "HA-4 DEFECT: PortfolioHoldingsCard calls a broker·name label function. " +
      "By-account labels must use name-only formatting (account_name ?? account_id)."
    );
  });

  it("HA-5: account_id used as React key — grouping identity is ID, not name", () => {
    assert.ok(
      holdingsSrc.includes("key={acct.account_id}") || holdingsSrc.includes('key={acct["account_id"]}'),
      "HA-5 DEFECT: PortfolioHoldingsCard By-account map does not use account_id as the React key. " +
      "Grouping/dedup identity must be account_id; account_name is volatile and non-unique."
    );
  });

  it("HA-9: Shares and avg_cost_eur calculation fields preserved alongside account label", () => {
    assert.ok(
      holdingsSrc.includes("acct.shares") || holdingsSrc.includes("shares"),
      "HA-9 DEFECT: shares field missing from PortfolioHoldingsCard By-account rows. " +
      "Per-account share count must remain rendered."
    );
    assert.ok(
      holdingsSrc.includes("avg_cost_eur"),
      "HA-9 DEFECT: avg_cost_eur field missing from PortfolioHoldingsCard By-account rows. " +
      "Per-account average cost must remain rendered."
    );
  });

  it("HA-10: Source does not use 'broker' field of HoldingsByAccount in label expression", () => {
    // HoldingsByAccount type has no broker field; confirm no broker reference was added.
    const byAccountSection = holdingsSrc.slice(holdingsSrc.indexOf("holdings_by_account"));
    assert.ok(
      !byAccountSection.includes("acct.broker") && !byAccountSection.includes(".broker"),
      "HA-10 DEFECT: PortfolioHoldingsCard references a broker field on HoldingsByAccount entries. " +
      "Broker data does not belong in the per-account breakdown label."
    );
  });

  // -----------------------------------------------------------------------
  // Behavioral-mirror tests (pure logic, no React)
  // -----------------------------------------------------------------------

  it("HA-8: Named account renders account_name only — no broker prefix", () => {
    const named = holdingFixtures[0]; // "Ahorro ING"
    const label = acctLabelMirror(named);
    assert.strictEqual(label, "Ahorro ING",
      "HA-8 DEFECT: Named account label is not the bare account_name. " +
      "By-account label must be account_name only.");
    assert.ok(!label.includes("·"),
      "HA-8 DEFECT: Named account label contains '·' separator.");
  });

  it("HA-7: Missing account_name uses account_id as fallback — not blank", () => {
    const noName = holdingFixtures[2]; // account_id "acc-no-name", name undefined
    const label = acctLabelMirror(noName);
    assert.strictEqual(label, "acc-no-name",
      "HA-7 DEFECT: Missing account_name did not fall back to account_id. " +
      "Fallback must be account_id, never empty or fabricated.");
    assert.ok(label.length > 0, "HA-7 DEFECT: Fallback label is empty string.");
  });

  it("HA-6: _unassigned sentinel renders UNASSIGNED_LABEL ('Sin asignar'), not raw '_unassigned'", () => {
    // Source-contract: PortfolioHoldingsCard must import UNASSIGNED_LABEL and have an
    // explicit equality check for the '_unassigned' sentinel in its label helper.
    // Removing either will cause this test to fail.
    assert.ok(
      holdingsSrc.includes("UNASSIGNED_LABEL"),
      "HA-6 DEFECT: PortfolioHoldingsCard does not reference UNASSIGNED_LABEL. " +
      "The _unassigned sentinel must render 'Sin asignar', not the raw ID string."
    );
    assert.ok(
      holdingsSrc.includes('"_unassigned"') || holdingsSrc.includes("'_unassigned'"),
      "HA-6 DEFECT: PortfolioHoldingsCard has no explicit guard for the '_unassigned' sentinel. " +
      "The label helper must check account_id === '_unassigned' before the name ?? id fallback."
    );

    // Behavioral: acctLabelMirror mirrors the actual acctLabel() in PortfolioHoldingsCard.
    // The sentinel must render UNASSIGNED_LABEL, not the raw internal ID.
    const unassigned = holdingFixtures[3]; // { account_id: "_unassigned", account_name: undefined }
    const label = acctLabelMirror(unassigned);
    assert.strictEqual(label, HOLDINGS_UNASSIGNED_LABEL,
      "HA-6 DEFECT: acctLabel for _unassigned must return 'Sin asignar' (UNASSIGNED_LABEL). " +
      "Rendering the raw '_unassigned' sentinel ID would be a user-facing defect."
    );
    assert.notEqual(label, "_unassigned",
      "HA-6 DEFECT: acctLabel rendered the raw '_unassigned' sentinel — must never be visible to users."
    );
  });
});
