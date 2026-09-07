/**
 * symbolConfigSectionContract.test.mjs — Symbol Configuration Section source-contract tests.
 *
 * Ref: danny-symbol-configuration-section-contract.md + Investments parent-menu directive
 *
 * Coverage:
 *   SC-1  SymbolConfigurationCard appears before PortfolioHoldingsCard in page.tsx (placement).
 *   SC-2  SymbolConfigurationCard is inside the `{stocksSecurityId && ...}` guard.
 *   SC-3  Identity fields (security_id, exchange_mic, ticker) use ReadOnlyField; no <input for them.
 *   SC-4  effective_yfinance_symbol and effective_tradingview_symbol are shown as read-only spans.
 *   SC-5  ToggleRow has role="switch" and aria-checked attributes.
 *   SC-6  ToggleRow supports disabled + disabledReason (rendered conditionally).
 *   SC-7  Section wrapper has aria-label="Symbol Configuration".
 *   SC-8  TopNav DROPDOWNS.Investments contains all required children (Symbols, Movements,
 *         Accounts, Calendar, Action Plans).
 *   SC-9  "Investments" nav item is a Dropdown (not a bare Link) — Symbols is a child, not
 *         a standalone top-level nav link labelled "Symbols".
 *   SC-10 No standalone top-level <Link ...>Symbols</Link> in the desktop nav.
 *
 * Run: node --test frontend/tests/symbolConfigSectionContract.test.mjs
 */

import { readFileSync } from "fs";
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");

const pageSrc = readFileSync(
  join(root, "src/app/symbols/[symbol]/page.tsx"),
  "utf8",
);
const cardSrc = readFileSync(
  join(root, "src/components/SymbolConfigurationCard.tsx"),
  "utf8",
);
const topNavSrc = readFileSync(
  join(root, "src/components/TopNav.tsx"),
  "utf8",
);

// ---------------------------------------------------------------------------
// SC-1 + SC-2: Component placement in page.tsx
// ---------------------------------------------------------------------------

describe("SC-1/SC-2: SymbolConfigurationCard placement in page.tsx", () => {
  it("SC-1: SymbolConfigurationCard appears before PortfolioHoldingsCard in source order", () => {
    // Search for JSX usage (opening tag), not just the import statement
    const scIdx = pageSrc.indexOf("<SymbolConfigurationCard");
    const phIdx = pageSrc.indexOf("<PortfolioHoldingsCard");
    assert.ok(scIdx !== -1, "<SymbolConfigurationCard JSX not found in page.tsx");
    assert.ok(phIdx !== -1, "<PortfolioHoldingsCard JSX not found in page.tsx");
    assert.ok(
      scIdx < phIdx,
      `SC-1 FAIL: <SymbolConfigurationCard (idx=${scIdx}) must appear before ` +
        `<PortfolioHoldingsCard (idx=${phIdx}) — contract requires Config first, Holdings second.`,
    );
  });

  it("SC-2: SymbolConfigurationCard is inside the stocksSecurityId guard", () => {
    const guardIdx = pageSrc.indexOf("stocksSecurityId &&");
    const scIdx = pageSrc.indexOf("<SymbolConfigurationCard");
    assert.ok(guardIdx !== -1, "stocksSecurityId guard not found in page.tsx");
    assert.ok(scIdx !== -1, "<SymbolConfigurationCard JSX not found in page.tsx");
    assert.ok(
      guardIdx < scIdx,
      `SC-2 FAIL: stocksSecurityId guard (idx=${guardIdx}) must appear before ` +
        `<SymbolConfigurationCard (idx=${scIdx}) — card must be inside the guard.`,
    );
  });
});

// ---------------------------------------------------------------------------
// SC-3: Identity fields are read-only (no <input for security_id / exchange_mic / ticker)
// ---------------------------------------------------------------------------

describe("SC-3: Identity fields rendered as ReadOnlyField, no input elements", () => {
  it("security_id label uses ReadOnlyField (not input)", () => {
    assert.ok(
      cardSrc.includes('ReadOnlyField') && cardSrc.includes('security_id'),
      "SymbolConfigurationCard must render security_id via ReadOnlyField",
    );
    // Confirm no editable <input with a security_id name/id attribute
    assert.ok(
      !/name=["']security_id["']/.test(cardSrc) && !/id=["']security_id["']/.test(cardSrc),
      "SC-3 FAIL: security_id must not be an editable input field",
    );
  });

  it("exchange_mic label uses ReadOnlyField (not input)", () => {
    assert.ok(
      cardSrc.includes('ReadOnlyField') && cardSrc.includes('exchange_mic'),
      "SymbolConfigurationCard must reference ReadOnlyField near exchange_mic",
    );
    assert.ok(
      !/name=["']exchange_mic["']/.test(cardSrc) && !/id=["']exchange_mic["']/.test(cardSrc),
      "SC-3 FAIL: exchange_mic must not be an editable input field",
    );
  });

  it('ReadOnlyField is defined as a pure display component (no input element inside it)', () => {
    // Extract the ReadOnlyField function body
    const fnStart = cardSrc.indexOf("function ReadOnlyField");
    assert.ok(fnStart !== -1, "ReadOnlyField not found");
    // Find the closing brace of the function (balanced brace search)
    let depth = 0;
    let bodyStart = cardSrc.indexOf("{", fnStart);
    let bodyEnd = bodyStart;
    for (let i = bodyStart; i < cardSrc.length; i++) {
      if (cardSrc[i] === "{") depth++;
      else if (cardSrc[i] === "}") {
        depth--;
        if (depth === 0) { bodyEnd = i; break; }
      }
    }
    const fnBody = cardSrc.slice(fnStart, bodyEnd + 1);
    assert.ok(
      !/<input/.test(fnBody),
      "SC-3 FAIL: ReadOnlyField must not contain an <input element",
    );
  });

  it("ticker is derived from security.ticker (not an editable input)", () => {
    // ticker derivation via security.ticker ?? ... should be present
    assert.ok(
      cardSrc.includes("security.ticker"),
      "SymbolConfigurationCard must read security.ticker for ticker derivation",
    );
    assert.ok(
      !/name=["']ticker["']/.test(cardSrc) && !/id=["']ticker["']/.test(cardSrc),
      "SC-3 FAIL: ticker must not be an editable input field",
    );
  });
});

// ---------------------------------------------------------------------------
// SC-4: effective symbols shown as read-only spans
// ---------------------------------------------------------------------------

describe("SC-4: effective symbols are read-only (span, not input)", () => {
  it("effective_yfinance_symbol displayed inside a <span>", () => {
    const yfinanceIdx = cardSrc.indexOf("effective_yfinance_symbol");
    assert.ok(yfinanceIdx !== -1, "effective_yfinance_symbol not found in card source");
    // Look for a <span> within a reasonable window around the reference
    const surroundingWindow = cardSrc.slice(
      Math.max(0, yfinanceIdx - 200),
      yfinanceIdx + 200,
    );
    assert.ok(
      surroundingWindow.includes("<span"),
      "SC-4 FAIL: effective_yfinance_symbol must be displayed inside a <span> (read-only)",
    );
    // No input for effective symbol
    assert.ok(
      !/name=["']effective_yfinance["']/.test(cardSrc),
      "SC-4 FAIL: effective_yfinance_symbol must not be an editable input",
    );
  });

  it("effective_tradingview_symbol displayed inside a <span>", () => {
    const tvIdx = cardSrc.indexOf("effective_tradingview_symbol");
    assert.ok(tvIdx !== -1, "effective_tradingview_symbol not found in card source");
    const surroundingWindow = cardSrc.slice(
      Math.max(0, tvIdx - 200),
      tvIdx + 200,
    );
    assert.ok(
      surroundingWindow.includes("<span"),
      "SC-4 FAIL: effective_tradingview_symbol must be displayed inside a <span> (read-only)",
    );
  });
});

// ---------------------------------------------------------------------------
// SC-5: ToggleRow ARIA attributes — toggles removed, ARIA patterns absent
// ---------------------------------------------------------------------------

describe("SC-5: ToggleRow has role=switch and aria-checked", () => {
  // ToggleRow and its ARIA attributes are intentionally absent from
  // SymbolConfigurationCard per the Symbol Details redesign directive
  // (Agent & Alert Toggles removed; see AT group in symbolDetailRedesign.test.mjs).
  // These tests now guard against re-introduction of toggle UI.
  it('role="switch" absent — toggle controls removed from SymbolConfigurationCard', () => {
    assert.ok(
      !cardSrc.includes('role="switch"'),
      'SC-5 DEFECT: role="switch" found in SymbolConfigurationCard. ' +
      'Toggle controls must be fully absent per the Symbol Details redesign directive.',
    );
  });

  it("aria-checked absent — toggle ARIA removed from SymbolConfigurationCard", () => {
    assert.ok(
      !cardSrc.includes("aria-checked"),
      "SC-5 DEFECT: aria-checked found in SymbolConfigurationCard. " +
      "Toggle controls must be fully absent per the Symbol Details redesign directive.",
    );
  });
});

// ---------------------------------------------------------------------------
// SC-6: ToggleRow disabled + disabledReason — removed from SymbolConfigurationCard
// ---------------------------------------------------------------------------

describe("SC-6: ToggleRow disabled prop and disabledReason conditional rendering", () => {
  // ToggleRow is intentionally absent from SymbolConfigurationCard; these tests
  // guard against the toggle-specific disabled/disabledReason logic being re-added.
  it("ToggleRow absent — toggle component not re-introduced", () => {
    assert.ok(
      !cardSrc.includes("ToggleRow"),
      "SC-6 DEFECT: ToggleRow re-introduced in SymbolConfigurationCard. " +
      "Toggle UI must remain absent per redesign directive.",
    );
  });

  it("disabledReason absent — toggle conditional rendering fully removed", () => {
    assert.ok(
      !cardSrc.includes("disabledReason"),
      "SC-6 DEFECT: disabledReason found in SymbolConfigurationCard. " +
      "Toggle-specific disabled logic must be absent — all toggle rendering removed.",
    );
  });

  it("disabledReason={!usOptionsEligible} toggle pattern absent", () => {
    assert.ok(
      !cardSrc.includes("disabledReason={!usOptionsEligible"),
      "SC-6 DEFECT: Toggle disabledReason prop pattern found in SymbolConfigurationCard. " +
      "All toggle-related rendering must be absent per redesign directive.",
    );
  });
});

// ---------------------------------------------------------------------------
// SC-7: Section wrapper aria-label
// ---------------------------------------------------------------------------

describe('SC-7: section wrapper has aria-label="Symbol Configuration"', () => {
  it('aria-label="Symbol Configuration" present on section or wrapper element', () => {
    assert.ok(
      cardSrc.includes('aria-label="Symbol Configuration"'),
      'SC-7 FAIL: SymbolConfigurationCard wrapper must have aria-label="Symbol Configuration"',
    );
  });
});

// ---------------------------------------------------------------------------
// SC-8: TopNav DROPDOWNS.Investments children
// ---------------------------------------------------------------------------

describe("SC-8: TopNav DROPDOWNS.Investments contains all required children", () => {
  const requiredLabels = ["Symbols", "Movements", "Accounts", "Calendar", "Action Plans"];

  for (const label of requiredLabels) {
    it(`DROPDOWNS.Investments includes child label="${label}"`, () => {
      // Find the DROPDOWNS definition block
      const dropdownsStart = topNavSrc.indexOf("const DROPDOWNS");
      assert.ok(dropdownsStart !== -1, "DROPDOWNS not found in TopNav.tsx");
      // Find the Investments array
      const investmentsStart = topNavSrc.indexOf("Investments:", dropdownsStart);
      assert.ok(investmentsStart !== -1, "Investments key not found in DROPDOWNS");
      // Capture the Investments array value (until the next top-level key or closing brace)
      const nextKeyMatch = topNavSrc.slice(investmentsStart + 12).search(/^\s{2}[A-Z]/m);
      const investmentsSlice = nextKeyMatch === -1
        ? topNavSrc.slice(investmentsStart)
        : topNavSrc.slice(investmentsStart, investmentsStart + 12 + nextKeyMatch);
      assert.ok(
        investmentsSlice.includes(`"${label}"`),
        `SC-8 FAIL: DROPDOWNS.Investments must include a child with label="${label}"`,
      );
    });
  }
});

// ---------------------------------------------------------------------------
// SC-9/SC-10: Investments is a Dropdown, not a standalone top-level Link
// ---------------------------------------------------------------------------

describe("SC-9/SC-10: Investments is a Dropdown; no bare top-level 'Symbols' Link", () => {
  it("SC-9: Investments is rendered as a <Dropdown> component in desktop nav", () => {
    assert.ok(
      topNavSrc.includes('label="Investments"') && topNavSrc.includes("DROPDOWNS.Investments"),
      'SC-9 FAIL: "Investments" must be rendered as a <Dropdown> with items=DROPDOWNS.Investments',
    );
  });

  it("SC-10: no standalone top-level <Link> with text 'Symbols' in the desktop nav", () => {
    // Find the desktop nav block — between hidden and md:flex div
    const desktopNavStart = topNavSrc.indexOf('hidden flex-wrap');
    assert.ok(desktopNavStart !== -1, "Desktop nav block not found");
    // A bare `<Link href="/symbols"...>Symbols</Link>` (not inside a Dropdown) would
    // indicate Symbols was promoted to a top-level nav item instead of staying as a child.
    // The Dropdown component renders its own internal links — any /symbols link in the
    // desktop nav should only appear inside DROPDOWNS.Investments, not as a direct <Link>.
    const desktopBlock = topNavSrc.slice(desktopNavStart, desktopNavStart + 1000);
    // A direct <Link href="/symbols"> followed shortly by Symbols text in the nav block
    // is the anti-pattern — Dropdown label "Investments" should be there instead.
    const hasDirectSymbolsLink = /<Link[^>]*href=["']\/symbols["'][^>]*>[\s\S]{0,20}Symbols/.test(desktopBlock);
    assert.ok(
      !hasDirectSymbolsLink,
      'SC-10 FAIL: A standalone top-level <Link href="/symbols">Symbols</Link> was found in ' +
        'the desktop nav — "Symbols" must be a child of the Investments Dropdown, not a direct link.',
    );
  });
});
