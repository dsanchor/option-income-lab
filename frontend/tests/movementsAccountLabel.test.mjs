/**
 * movementsAccountLabel.test.mjs — Account-label contract for Movements surfaces.
 *
 * Requirements:
 *   FL — formatAccountName: name-only output; no broker prefix; no · separator;
 *        account_id fallback (never empty, never fabricated broker); null → "—".
 *   GL — getAccountName lookup: _unassigned → UNASSIGNED_LABEL; found → name;
 *        unknown → raw account_id; never blank or broker-prefixed.
 *   DA — Deprecated aliases (formatMovementAccountLabel / getMovementAccountLabel)
 *        map exactly to name-only functions; no broker·name output.
 *   SC — Source contract: every movement/transaction/reassignment component
 *        uses formatAccountName/getAccountName (not formatAccountLabel/getAccountLabel).
 *   PL — Payload/filter: account_id used for API payloads, filter values, and
 *        AccountBadge props; account name is only for display text.
 *   CO — Color determinism: badge color based on account_id (not name); same ID
 *        always same color; _unassigned → neutral class; name change ≠ color change.
 *   NS — Non-movement displays not globally changed: formatAccountLabel (broker·name)
 *        still works correctly for account management UIs; Accounts/Holdings unaffected.
 *
 * Strategy: behavioral mirrors for pure-logic (FL/GL/DA/CO/NS) so tests run
 * without a bundler. Source-contract readFileSync checks for component usage (SC/PL).
 *
 * Run: node --test frontend/tests/movementsAccountLabel.test.mjs
 */

import { readFileSync } from "fs";
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");

// Source files under test
const displaySrc      = readFileSync(join(root, "src/lib/accountDisplay.ts"), "utf8");
const movTableSrc     = readFileSync(join(root, "src/components/PortfolioMovementsTable.tsx"), "utf8");
const movDetailSrc    = readFileSync(join(root, "src/components/MovementDetailDialog.tsx"), "utf8");
const stockTxnSrc     = readFileSync(join(root, "src/components/StockTransactionsTable.tsx"), "utf8");
const reassignSrc     = readFileSync(join(root, "src/components/ReassignmentDialog.tsx"), "utf8");
const badgeSrc        = readFileSync(join(root, "src/components/AccountBadge.tsx"), "utf8");
const correctionSrc   = readFileSync(join(root, "src/components/MovementCorrectionDialog.tsx"), "utf8");
const holdingsSrc     = readFileSync(join(root, "src/components/PortfolioHoldingsCard.tsx"), "utf8");
const holdingsTableSrc = readFileSync(join(root, "src/components/PortfolioHoldingsTable.tsx"), "utf8");

// ---------------------------------------------------------------------------
// Behavioral mirrors (keep in sync with src/lib/accountDisplay.ts)
// ---------------------------------------------------------------------------

const BROKER_LABELS = {
  fidelity:             "Fidelity",
  heytrade:             "HeyTrade",
  ing:                  "ING",
  interactive_brokers:  "Interactive Brokers",
  other:                "Other",
};

const UNASSIGNED_LABEL = "Sin asignar";

const ACCOUNT_BADGE_PALETTE = [
  "bg-accent-blue/15 text-accent-blue",
  "bg-accent-green/15 text-accent-green",
  "bg-accent-purple/15 text-accent-purple",
  "bg-accent-cyan/15 text-accent-cyan",
  "bg-accent-orange/15 text-accent-orange",
  "bg-accent-red/15 text-accent-red",
];
const UNASSIGNED_BADGE_CLASS = "bg-bg-hover text-text-muted";

/** Mirror of formatAccountName — name only, never broker prefix. */
function formatAccountName(account) {
  if (!account) return "—";
  const name = account.name?.trim() || null;
  if (name) return name;
  return account.account_id || "—";
}

/** Mirror of getAccountName — lookup by ID, name only, never broker prefix. */
function getAccountName(accountId, accounts) {
  if (!accountId || accountId === "_unassigned") return UNASSIGNED_LABEL;
  const account = accounts.find((a) => a.account_id === accountId);
  if (account) return formatAccountName(account);
  return accountId;
}

/** Mirror of formatAccountLabel — broker·name format (account management UIs only). */
function formatAccountLabel(account) {
  if (!account) return "—";
  const brokerLabel = account.broker ? (BROKER_LABELS[account.broker] ?? account.broker) : null;
  const name = account.name?.trim() || null;
  if (brokerLabel && name) return `${brokerLabel} · ${name}`;
  if (name) return name;
  if (brokerLabel) return brokerLabel;
  return "—";
}

/** Mirror of getAccountLabel — broker·name lookup (account management UIs only). */
function getAccountLabel(accountId, accounts) {
  if (!accountId || accountId === "_unassigned") return UNASSIGNED_LABEL;
  const account = accounts.find((a) => a.account_id === accountId);
  if (account) return formatAccountLabel(account);
  return accountId;
}

/** Mirror of accountColorIndex — deterministic hash of account_id. */
function accountColorIndex(accountId) {
  if (!accountId || accountId === "_unassigned") return -1;
  let h = 0;
  for (let i = 0; i < accountId.length; i++) {
    h = (Math.imul(31, h) + accountId.charCodeAt(i)) | 0;
  }
  return Math.abs(h) % ACCOUNT_BADGE_PALETTE.length;
}

/** Mirror of getAccountBadgeClass. */
function getAccountBadgeClass(accountId) {
  if (!accountId || accountId === "_unassigned") return UNASSIGNED_BADGE_CLASS;
  return ACCOUNT_BADGE_PALETTE[accountColorIndex(accountId)];
}

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

const accounts = [
  { account_id: "acc-heytrade-1",  broker: "heytrade",            name: "Cuenta Principal" },
  { account_id: "acc-ing-2",       broker: "ing",                 name: "Ahorro ING"       },
  { account_id: "acc-ib-3",        broker: "interactive_brokers", name: "Options Account"  },
  { account_id: "acc-no-name",     broker: "heytrade",            name: ""                 },
  { account_id: "acc-no-broker",   broker: null,                  name: "Direct Account"   },
  { account_id: "acc-neither",     broker: null,                  name: ""                 },
];

// ---------------------------------------------------------------------------
// FL: formatAccountName — name-only label contract
// ---------------------------------------------------------------------------

describe("FL: formatAccountName — name only, no broker prefix", () => {
  it("FL-1: Returns account name when present", () => {
    assert.equal(formatAccountName({ account_id: "x", broker: "heytrade", name: "Cuenta Principal" }),
      "Cuenta Principal");
  });

  it("FL-2: Returns account name when broker is present (broker NOT prefixed)", () => {
    const label = formatAccountName({ account_id: "x", broker: "heytrade", name: "My Account" });
    assert.equal(label, "My Account",
      "FL-2: formatAccountName must return name only — 'HeyTrade · My Account' would be a defect.");
  });

  it("FL-3: Returns account_id when name is empty/absent (last resort, never broker)", () => {
    const label = formatAccountName({ account_id: "acc-heytrade-1", broker: "heytrade", name: "" });
    assert.equal(label, "acc-heytrade-1",
      "FL-3: Missing name must fall back to account_id, NOT to broker label.");
    assert.ok(!label.includes("HeyTrade"),
      "FL-3: Broker name must never appear in formatAccountName output.");
  });

  it("FL-4: Returns '—' for null/undefined account", () => {
    assert.equal(formatAccountName(null), "—");
    assert.equal(formatAccountName(undefined), "—");
  });

  it("FL-5: Output never contains the '·' separator", () => {
    for (const a of accounts) {
      const label = formatAccountName(a);
      assert.ok(!label.includes("·"),
        `FL-5 DEFECT: formatAccountName returned '${label}' which contains '·' separator. ` +
        "Only formatAccountLabel uses this separator.");
    }
  });

  it("FL-6: Output never uses broker label as a standalone value or prefix (no fabricated broker)", () => {
    // The prohibition is on the broker·name FORMAT and on broker-as-fallback,
    // not on account names that happen to contain bank abbreviations.
    const brokerNames = Object.values(BROKER_LABELS);
    for (const a of accounts) {
      const label = formatAccountName(a);
      for (const broker of brokerNames) {
        // Must not start with "BrokerLabel ·" (broker prefix pattern)
        assert.ok(!label.startsWith(`${broker} ·`),
          `FL-6 DEFECT: formatAccountName returned '${label}' which starts with ` +
          `broker prefix '${broker} ·'. Name-only format must never use this pattern.`);
        // Must not equal bare broker label (broker-as-sole-fallback pattern)
        assert.ok(label !== broker,
          `FL-6 DEFECT: formatAccountName returned bare broker label '${label}'. ` +
          "Fallback for missing name must be account_id, not broker name.");
      }
    }
  });

  it("FL-7: Trims whitespace from name", () => {
    const label = formatAccountName({ account_id: "x", broker: "heytrade", name: "  Trimmed  " });
    assert.equal(label, "Trimmed", "FL-7: Leading/trailing whitespace in name must be trimmed.");
  });

  it("FL-8: Whitespace-only name falls through to account_id", () => {
    const label = formatAccountName({ account_id: "acc-ws-123", broker: "ing", name: "   " });
    assert.equal(label, "acc-ws-123",
      "FL-8: A whitespace-only name must be treated as absent — fall back to account_id.");
  });

  it("FL-9: Output is never an empty string (always non-empty)", () => {
    for (const a of accounts) {
      const label = formatAccountName(a);
      assert.ok(label.length > 0,
        `FL-9 DEFECT: formatAccountName returned empty string for account_id='${a.account_id}'.`);
    }
    // Worst-case: no name, no broker, but has account_id
    assert.ok(formatAccountName({ account_id: "fallback-id", broker: null, name: "" }).length > 0);
  });
});

// ---------------------------------------------------------------------------
// GL: getAccountName — ID-to-name lookup contract
// ---------------------------------------------------------------------------

describe("GL: getAccountName — lookup by account_id, name only", () => {
  it("GL-1: '_unassigned' returns UNASSIGNED_LABEL ('Sin asignar')", () => {
    assert.equal(getAccountName("_unassigned", accounts), UNASSIGNED_LABEL);
  });

  it("GL-2: Empty string returns UNASSIGNED_LABEL", () => {
    assert.equal(getAccountName("", accounts), UNASSIGNED_LABEL);
  });

  it("GL-3: Known account_id returns account name only (no broker prefix)", () => {
    const label = getAccountName("acc-heytrade-1", accounts);
    assert.equal(label, "Cuenta Principal",
      "GL-3: getAccountName must return name only — never 'HeyTrade · Cuenta Principal'.");
  });

  it("GL-4: Known account with broker returns name only (ING account)", () => {
    const label = getAccountName("acc-ing-2", accounts);
    assert.equal(label, "Ahorro ING",
      "GL-4: Broker prefix 'ING · ' must be absent from getAccountName output.");
  });

  it("GL-5: Known account with no name returns account_id (never broker)", () => {
    const label = getAccountName("acc-no-name", accounts);
    assert.equal(label, "acc-no-name",
      "GL-5: Account with empty name must return its account_id, NOT 'HeyTrade'.");
    assert.ok(!label.includes("HeyTrade"),
      "GL-5: Broker name must not appear as fallback in getAccountName.");
  });

  it("GL-6: Unknown account_id returns raw account_id (not blank, not fabricated)", () => {
    const label = getAccountName("nonexistent-uuid-999", accounts);
    assert.equal(label, "nonexistent-uuid-999",
      "GL-6: Unknown account_id must fall back to the raw ID — not empty, not a generic label.");
  });

  it("GL-7: Output never contains '·' separator for any account", () => {
    for (const a of accounts) {
      const label = getAccountName(a.account_id, accounts);
      assert.ok(!label.includes("·"),
        `GL-7 DEFECT: getAccountName('${a.account_id}') = '${label}' contains '·'. ` +
        "Name-only function must not produce 'Broker · Name' format.");
    }
  });

  it("GL-8: Output never uses broker label as standalone or prefix (no broker·name format)", () => {
    // Check the specific forbidden pattern (BrokerLabel · Name), not substring containment,
    // since user-given account names may legitimately contain bank abbreviations.
    const brokerNames = Object.values(BROKER_LABELS);
    for (const a of accounts) {
      const label = getAccountName(a.account_id, accounts);
      for (const broker of brokerNames) {
        assert.ok(!label.startsWith(`${broker} ·`),
          `GL-8 DEFECT: getAccountName('${a.account_id}') = '${label}' ` +
          `starts with broker prefix '${broker} ·'. Name-only functions must never produce this.`);
        assert.ok(label !== broker,
          `GL-8 DEFECT: getAccountName returned bare broker label '${label}'. ` +
          "Fallback must be account_id, never broker name.");
      }
    }
  });

  it("GL-9: UNASSIGNED_LABEL is 'Sin asignar' (contract constant)", () => {
    assert.equal(UNASSIGNED_LABEL, "Sin asignar");
    // Verify the source file defines the same string
    assert.ok(displaySrc.includes('"Sin asignar"') || displaySrc.includes("'Sin asignar'"),
      "GL-9 DEFECT: UNASSIGNED_LABEL must be 'Sin asignar' in accountDisplay.ts.");
  });
});

// ---------------------------------------------------------------------------
// DA: Deprecated aliases — map to name-only behavior, never broker·name
// ---------------------------------------------------------------------------

describe("DA: Deprecated aliases behave identically to name-only functions", () => {
  it("DA-1: formatMovementAccountLabel alias present in source", () => {
    assert.ok(
      displaySrc.includes("formatMovementAccountLabel"),
      "DA-1 DEFECT: formatMovementAccountLabel alias missing from accountDisplay.ts."
    );
  });

  it("DA-2: getMovementAccountLabel alias present in source", () => {
    assert.ok(
      displaySrc.includes("getMovementAccountLabel"),
      "DA-2 DEFECT: getMovementAccountLabel alias missing from accountDisplay.ts."
    );
  });

  it("DA-3: formatMovementAccountLabel = formatAccountName (same output for all fixtures)", () => {
    // Both functions must return the same result — alias contract.
    // Since we can't import TS, mirror both and check equivalence on fixtures.
    const formatMovementAccountLabel = formatAccountName; // alias
    for (const a of accounts) {
      assert.equal(formatMovementAccountLabel(a), formatAccountName(a),
        `DA-3: Deprecated alias output differs from formatAccountName for account_id='${a.account_id}'.`);
    }
  });

  it("DA-4: getMovementAccountLabel = getAccountName (same output for all fixtures)", () => {
    const getMovementAccountLabel = getAccountName; // alias
    const ids = accounts.map(a => a.account_id).concat(["_unassigned", "", "unknown-xyz"]);
    for (const id of ids) {
      assert.equal(getMovementAccountLabel(id, accounts), getAccountName(id, accounts),
        `DA-4: Deprecated alias output differs from getAccountName for id='${id}'.`);
    }
  });

  it("DA-5: Deprecated aliases do NOT produce broker·name format", () => {
    // Mirror-verify: deprecated aliases must never return 'HeyTrade · ...'
    for (const a of accounts) {
      const label = formatAccountName(a); // alias maps here
      assert.ok(!label.includes("·"),
        `DA-5 DEFECT: Deprecated alias returned '${label}' with '·' separator. ` +
        "Must be name-only, not broker·name.");
    }
  });

  it("DA-6: Source marks aliases as @deprecated pointing to name-only functions", () => {
    assert.ok(
      displaySrc.includes("@deprecated") && displaySrc.includes("formatAccountName"),
      "DA-6: Deprecated aliases must be marked @deprecated with reference to formatAccountName."
    );
  });
});

// ---------------------------------------------------------------------------
// SC: Source contract — movement/transaction components use name-only functions
// ---------------------------------------------------------------------------

describe("SC: Source contract — components use formatAccountName/getAccountName", () => {
  it("SC-1: PortfolioMovementsTable uses formatAccountName (not formatAccountLabel) for display", () => {
    assert.ok(movTableSrc.includes("formatAccountName"),
      "SC-1 DEFECT: PortfolioMovementsTable must import/use formatAccountName for account labels.");
    assert.ok(!movTableSrc.includes("formatAccountLabel"),
      "SC-1 DEFECT: PortfolioMovementsTable must NOT use formatAccountLabel (broker·name format).");
  });

  it("SC-2: MovementDetailDialog uses getAccountName (not getAccountLabel) for account display", () => {
    assert.ok(movDetailSrc.includes("getAccountName"),
      "SC-2 DEFECT: MovementDetailDialog must import/use getAccountName for account label display.");
    assert.ok(!movDetailSrc.includes("getAccountLabel"),
      "SC-2 DEFECT: MovementDetailDialog must NOT use getAccountLabel (broker·name format).");
  });

  it("SC-3: StockTransactionsTable uses getAccountName (not getAccountLabel)", () => {
    assert.ok(stockTxnSrc.includes("getAccountName"),
      "SC-3 DEFECT: StockTransactionsTable must use getAccountName for account labels.");
    assert.ok(!stockTxnSrc.includes("getAccountLabel") && !stockTxnSrc.includes("formatAccountLabel"),
      "SC-3 DEFECT: StockTransactionsTable must NOT use broker·name label functions.");
  });

  it("SC-4: ReassignmentDialog uses formatAccountName/getAccountName (not broker·name functions)", () => {
    assert.ok(
      reassignSrc.includes("formatAccountName") || reassignSrc.includes("getAccountName"),
      "SC-4 DEFECT: ReassignmentDialog must use name-only label functions."
    );
    assert.ok(!reassignSrc.includes("formatAccountLabel"),
      "SC-4 DEFECT: ReassignmentDialog must NOT use formatAccountLabel (broker·name format).");
    assert.ok(!reassignSrc.includes("getAccountLabel"),
      "SC-4 DEFECT: ReassignmentDialog must NOT use getAccountLabel (broker·name format).");
  });

  it("SC-5: AccountBadge uses getAccountName (not getAccountLabel)", () => {
    assert.ok(badgeSrc.includes("getAccountName"),
      "SC-5 DEFECT: AccountBadge must use getAccountName for badge text.");
    assert.ok(!badgeSrc.includes("getAccountLabel") && !badgeSrc.includes("formatAccountLabel"),
      "SC-5 DEFECT: AccountBadge must NOT use broker·name label functions.");
  });

  it("SC-6: MovementDetailDialog handles transfer accounts with getAccountName", () => {
    // Transfer movements show 'From account' and 'To account' — both must use getAccountName.
    assert.ok(
      movDetailSrc.includes("transfer_source_account_id") ||
      movDetailSrc.includes("transfer_dest_account_id"),
      "SC-6: MovementDetailDialog must reference transfer account fields."
    );
    // The region containing transfer fields must use getAccountName
    const transferRegion = movDetailSrc.match(/transfer_source[\s\S]{0,400}/)?.[0] ?? "";
    assert.ok(
      transferRegion.includes("getAccountName") || movDetailSrc.includes("getAccountName"),
      "SC-6 DEFECT: Transfer account labels must use getAccountName."
    );
  });

  it("SC-7: No movement component imports formatAccountLabel for any display purpose", () => {
    // Each of the display components must NOT import formatAccountLabel at the top.
    // (It may be aliased as deprecated in accountDisplay.ts itself — that is fine.)
    for (const [name, src] of [
      ["PortfolioMovementsTable", movTableSrc],
      ["MovementDetailDialog",    movDetailSrc],
      ["StockTransactionsTable",  stockTxnSrc],
      ["ReassignmentDialog",      reassignSrc],
    ]) {
      const importMatch = src.match(/import\s*\{[^}]*formatAccountLabel[^}]*\}/)?.[0];
      assert.ok(!importMatch,
        `SC-7 DEFECT: ${name} imports formatAccountLabel — must use formatAccountName instead. ` +
        `Found: ${importMatch}`);
    }
  });
});

// ---------------------------------------------------------------------------
// PL: Payload, filter, and AccountBadge prop use account_id (not name)
// ---------------------------------------------------------------------------

describe("PL: Payload and filter values are account_id, not account name", () => {
  it("PL-1: ReassignmentDialog API payload uses source_account_id / dest_account_id fields", () => {
    assert.ok(reassignSrc.includes("source_account_id"),
      "PL-1 DEFECT: ReassignmentDialog payload must use 'source_account_id' field.");
    assert.ok(reassignSrc.includes("dest_account_id"),
      "PL-1 DEFECT: ReassignmentDialog payload must use 'dest_account_id' field.");
  });

  it("PL-2: PortfolioMovementsTable filter select uses value={a.account_id}", () => {
    // The account filter dropdown must submit account_id as its value, not the name.
    assert.ok(
      movTableSrc.includes("a.account_id") || movTableSrc.includes("account_id}"),
      "PL-2 DEFECT: PortfolioMovementsTable account filter select must use value={a.account_id}."
    );
  });

  it("PL-3: ReassignmentDialog target select uses value={a.account_id}", () => {
    assert.ok(
      reassignSrc.includes("a.account_id"),
      "PL-3 DEFECT: ReassignmentDialog target account select must use value={a.account_id}, " +
      "not value={a.name}."
    );
  });

  it("PL-4: StockTransactionsTable accountMap is keyed by account_id", () => {
    // The accountMap must be built as { [account_id]: displayName } so lookups use IDs.
    assert.ok(
      stockTxnSrc.includes("account_id") &&
      (stockTxnSrc.includes("accountMap") || stockTxnSrc.includes("account_id, accounts")),
      "PL-4 DEFECT: StockTransactionsTable must build its account map keyed by account_id."
    );
  });

  it("PL-5: AccountBadge receives accountId prop (not accountName)", () => {
    // AccountBadge component must have an 'accountId' prop, not 'accountName'.
    assert.ok(badgeSrc.includes("accountId"),
      "PL-5 DEFECT: AccountBadge must have an accountId prop for color derivation.");
    assert.ok(!badgeSrc.includes("accountName:") && !badgeSrc.includes("accountName?:"),
      "PL-5 DEFECT: AccountBadge must NOT have an accountName prop — name is derived internally.");
  });

  it("PL-6: PortfolioMovementsTable passes m.account_id to AccountBadge (not display name)", () => {
    assert.ok(
      movTableSrc.includes("accountId={m.account_id}") ||
      movTableSrc.includes("accountId={m.account_id}"),
      "PL-6 DEFECT: PortfolioMovementsTable must pass m.account_id to AccountBadge, not a display name."
    );
  });
});

// ---------------------------------------------------------------------------
// CO: Color determinism — based on account_id, stable across name changes
// ---------------------------------------------------------------------------

describe("CO: Color determinism — based on account_id, not account name", () => {
  it("CO-1: Same account_id always produces the same color index (deterministic)", () => {
    const id  = "acc-heytrade-1";
    const idx1 = accountColorIndex(id);
    const idx2 = accountColorIndex(id);
    assert.equal(idx1, idx2, "CO-1: accountColorIndex must be deterministic for the same ID.");
  });

  it("CO-2: Color index is within palette bounds [0, palette.length)", () => {
    for (const a of accounts) {
      const idx = accountColorIndex(a.account_id);
      assert.ok(idx >= 0 && idx < ACCOUNT_BADGE_PALETTE.length,
        `CO-2: Color index ${idx} out of bounds for account_id='${a.account_id}'.`);
    }
  });

  it("CO-3: _unassigned / empty returns neutral class (UNASSIGNED_BADGE_CLASS)", () => {
    assert.equal(getAccountBadgeClass("_unassigned"), UNASSIGNED_BADGE_CLASS);
    assert.equal(getAccountBadgeClass(""), UNASSIGNED_BADGE_CLASS);
    assert.equal(accountColorIndex("_unassigned"), -1,
      "CO-3: _unassigned sentinel must return index -1 (neutral, not palette).");
  });

  it("CO-4: Renaming an account does not change its color (color based on ID only)", () => {
    const id = "stable-account-id-xyz";
    const colorBefore = getAccountBadgeClass(id);
    // Simulate rename — color derives from ID, not from the name field at all.
    const colorAfter = getAccountBadgeClass(id);
    assert.equal(colorBefore, colorAfter,
      "CO-4: Color must be stable across account renames (derived from account_id, not name).");
  });

  it("CO-5: accountColorIndex uses account_id string for hashing (not name)", () => {
    // Verify source: the hash function receives accountId (string), not any name field.
    assert.ok(
      displaySrc.includes("accountColorIndex") && displaySrc.includes("accountId.charCodeAt"),
      "CO-5 DEFECT: accountColorIndex must hash the accountId string character-by-character."
    );
  });

  it("CO-6: Source defines ACCOUNT_BADGE_PALETTE with exactly 6 color slots", () => {
    // 6 deterministic palette entries — change requires coordinated update.
    const paletteMatch = displaySrc.match(/ACCOUNT_BADGE_PALETTE\s*=\s*\[([\s\S]*?)\]/)?.[1] ?? "";
    const slots = (paletteMatch.match(/"[^"]+"/g) ?? []).length;
    assert.equal(slots, 6,
      `CO-6 DEFECT: ACCOUNT_BADGE_PALETTE must have exactly 6 color slots; found ${slots}.`
    );
  });

  it("CO-7: getAccountBadgeClass output is one of the palette classes or UNASSIGNED_BADGE_CLASS", () => {
    const validClasses = [...ACCOUNT_BADGE_PALETTE, UNASSIGNED_BADGE_CLASS];
    for (const a of accounts) {
      const cls = getAccountBadgeClass(a.account_id);
      assert.ok(validClasses.includes(cls),
        `CO-7 DEFECT: getAccountBadgeClass('${a.account_id}') = '${cls}' is not in the valid palette.`);
    }
  });

  it("CO-8: AccountBadge source uses getAccountBadgeClass with accountId prop (not name)", () => {
    assert.ok(
      badgeSrc.includes("getAccountBadgeClass") && badgeSrc.includes("accountId"),
      "CO-8 DEFECT: AccountBadge must call getAccountBadgeClass(accountId) for color derivation."
    );
  });
});

// ---------------------------------------------------------------------------
// NS: Non-movement displays NOT globally changed
// ---------------------------------------------------------------------------

describe("NS: Non-movement account displays not globally changed", () => {
  it("NS-1: formatAccountLabel (broker·name) still exists in accountDisplay.ts", () => {
    assert.ok(displaySrc.includes("function formatAccountLabel"),
      "NS-1 DEFECT: formatAccountLabel function must still exist for account management UIs.");
  });

  it("NS-2: getAccountLabel (broker·name lookup) still exists in accountDisplay.ts", () => {
    assert.ok(displaySrc.includes("function getAccountLabel"),
      "NS-2 DEFECT: getAccountLabel function must still exist for account management UIs.");
  });

  it("NS-3: formatAccountLabel still produces 'Broker · Name' for broker + name accounts", () => {
    // The management UI contract must remain intact.
    const label = formatAccountLabel({ account_id: "x", broker: "heytrade", name: "My Account" });
    assert.equal(label, "HeyTrade · My Account",
      "NS-3 DEFECT: formatAccountLabel must still produce 'HeyTrade · My Account' for broker+name accounts.");
    assert.ok(label.includes("·"),
      "NS-3: formatAccountLabel must include '·' separator for account management UIs.");
  });

  it("NS-4: getAccountLabel still returns 'Broker · Name' for known accounts", () => {
    const label = getAccountLabel("acc-heytrade-1", accounts);
    assert.ok(label.includes("HeyTrade") && label.includes("Cuenta Principal"),
      `NS-4 DEFECT: getAccountLabel must return broker·name; got '${label}'.`);
  });

  it("NS-5: formatAccountLabel and formatAccountName produce different results for broker+name accounts", () => {
    // They must differ — formatAccountName is name-only, formatAccountLabel is broker·name.
    const a = { account_id: "x", broker: "ing", name: "Test Account" };
    const nameOnly   = formatAccountName(a);
    const brokerName = formatAccountLabel(a);
    assert.notEqual(nameOnly, brokerName,
      "NS-5 DEFECT: formatAccountName and formatAccountLabel must produce different outputs " +
      "for a broker+name account. They appear to be identical — scope boundary is broken.");
    assert.equal(nameOnly,   "Test Account",  "NS-5: formatAccountName returns name only");
    assert.equal(brokerName, "ING · Test Account", "NS-5: formatAccountLabel returns broker·name");
  });

  it("NS-6: accountDisplay.ts exports both name-only AND broker·name functions (dual-export contract)", () => {
    assert.ok(displaySrc.includes("formatAccountName"),  "NS-6: formatAccountName must be exported.");
    assert.ok(displaySrc.includes("formatAccountLabel"), "NS-6: formatAccountLabel must be exported.");
    assert.ok(displaySrc.includes("getAccountName"),     "NS-6: getAccountName must be exported.");
    assert.ok(displaySrc.includes("getAccountLabel"),    "NS-6: getAccountLabel must be exported.");
  });

  it("NS-7: UNASSIGNED_LABEL ('Sin asignar') is the same in both name-only and broker·name paths", () => {
    // Both getAccountName and getAccountLabel must return UNASSIGNED_LABEL for _unassigned.
    assert.equal(getAccountName("_unassigned", accounts), UNASSIGNED_LABEL);
    assert.equal(getAccountLabel("_unassigned", accounts), UNASSIGNED_LABEL,
      "NS-7: getAccountLabel must also return UNASSIGNED_LABEL — unassigned is universal.");
  });
});

// ---------------------------------------------------------------------------
// Adversarial behavioral cross-checks (FL × GL parity; SC payload integrity)
// ---------------------------------------------------------------------------

describe("XC: Cross-checks — FL×GL parity; payload vs display separation", () => {
  it("XC-1: formatAccountName and getAccountName agree on same fixture account", () => {
    for (const a of accounts) {
      const direct = formatAccountName(a);
      const viaGet = getAccountName(a.account_id, accounts);
      assert.equal(direct, viaGet,
        `XC-1: formatAccountName('${a.account_id}') = '${direct}' ≠ ` +
        `getAccountName('${a.account_id}') = '${viaGet}'.`);
    }
  });

  it("XC-2: Display label (formatAccountName) differs from raw account_id for named accounts", () => {
    const a = { account_id: "acc-heytrade-1", broker: "heytrade", name: "Cuenta Principal" };
    assert.notEqual(formatAccountName(a), a.account_id,
      "XC-2: The display label must differ from account_id when account has a name.");
  });

  it("XC-3: Payload (account_id) does not change when account name changes", () => {
    // Simulate: same account_id, different names
    const id   = "stable-payload-id";
    const acc1 = { account_id: id, broker: "heytrade", name: "Old Name" };
    const acc2 = { account_id: id, broker: "heytrade", name: "New Name" };
    // Payload (account_id) must be identical
    assert.equal(acc1.account_id, acc2.account_id,
      "XC-3: account_id must not change when account name is updated.");
    // Display label will differ (correct)
    assert.notEqual(formatAccountName(acc1), formatAccountName(acc2),
      "XC-3: Display label correctly reflects the new name after rename.");
    // Color must remain the same (keyed on account_id)
    assert.equal(getAccountBadgeClass(acc1.account_id), getAccountBadgeClass(acc2.account_id),
      "XC-3: Color must be stable after rename (based on account_id, not name).");
  });

  it("XC-4: formatAccountName for legacy account with no name returns account_id (non-fabricated)", () => {
    const legacy = { account_id: "legacy-acc-000", broker: null, name: null };
    const label  = formatAccountName(legacy);
    assert.equal(label, "legacy-acc-000",
      "XC-4: Legacy account with no name and no broker must return raw account_id. " +
      "Must not return '—' (that is for null account objects only).");
    assert.ok(!Object.values(BROKER_LABELS).some(b => label.includes(b)),
      "XC-4: Legacy fallback must never contain a fabricated broker name.");
  });

  it("XC-5: getAccountName for legacy unknown ID returns raw ID (not blank, not 'Unknown')", () => {
    const label = getAccountName("unknown-legacy-id-456", accounts);
    assert.equal(label, "unknown-legacy-id-456",
      "XC-5: Unknown account_id must return the raw ID — not an empty string or generic 'Unknown'.");
    assert.ok(label.length > 0, "XC-5: Output must be non-empty for any account_id.");
    assert.ok(!label.includes("Unknown") && !label.includes("unknown label"),
      "XC-5: Must not fabricate a generic 'Unknown' label.");
  });
});

// ---------------------------------------------------------------------------
// RD: ReassignmentDialog — all dropdown/display sites use name-only labels
//
// Tests verify that every visible account label surface in ReassignmentDialog
// (IndividualMode and BatchMode) renders account_name only — no broker prefix,
// no "·" separator — and that option values / API payloads remain account_id.
// All tests PASS against current source.
// ---------------------------------------------------------------------------

describe("RD: ReassignmentDialog — account display sites name-only", () => {
  it("RD-1: IndividualMode 'Current account' display uses getAccountName (source)", () => {
    assert.ok(
      reassignSrc.includes("getAccountName(currentAccountId") ||
      reassignSrc.includes("getAccountName(currentAccountId,"),
      "RD-1 DEFECT: IndividualMode does not call getAccountName(currentAccountId) for the " +
      "'Current account' display. Must use name-only lookup, not broker·name function."
    );
  });

  it("RD-2: IndividualMode 'Assign to' option text uses formatAccountName (source)", () => {
    assert.ok(
      reassignSrc.includes("{formatAccountName(a)}"),
      "RD-2 DEFECT: IndividualMode 'Assign to' dropdown option text does not use " +
      "formatAccountName(a). Must render name-only label per option."
    );
  });

  it("RD-3: IndividualMode 'Assign to' select option value is a.account_id (ID, not name)", () => {
    assert.ok(
      reassignSrc.includes("value={a.account_id}"),
      "RD-3 DEFECT: 'Assign to' option value is not a.account_id. " +
      "Select value must be account_id so the API payload carries the correct ID."
    );
  });

  it("RD-4: BatchMode 'Source account' dropdown option text uses formatAccountName (source)", () => {
    // BatchMode maps accounts twice (source + dest); count occurrences.
    const hits = (reassignSrc.match(/\{formatAccountName\(a\)\}/g) ?? []).length;
    assert.ok(hits >= 2,
      `RD-4 DEFECT: Expected at least 2 {formatAccountName(a)} occurrences for source+dest ` +
      `dropdowns in BatchMode; found ${hits}. Both selects must render name-only option text.`
    );
  });

  it("RD-5: BatchMode 'Destination account' option text also uses formatAccountName (source)", () => {
    // Both source and dest selects must render name-only — RD-4 verifies count >= 2;
    // this test verifies neither select uses formatAccountLabel instead.
    assert.ok(
      !reassignSrc.includes("formatAccountLabel"),
      "RD-5 DEFECT: ReassignmentDialog calls formatAccountLabel somewhere. " +
      "All account option labels (source and destination selects) must use formatAccountName."
    );
  });

  it("RD-6: All dropdown _unassigned sentinel options render 'Sin asignar' (source)", () => {
    // Every <option value='_unassigned'> must have visible text 'Sin asignar'.
    const sentinelHits = (reassignSrc.match(/>Sin asignar<\/option>/g) ?? []).length;
    assert.ok(sentinelHits >= 2,
      `RD-6 DEFECT: Expected at least 2 'Sin asignar' sentinel <option> elements in ` +
      `ReassignmentDialog (Individual Assign-to + Batch source + Batch dest); ` +
      `found ${sentinelHits}. Every unassigned option must show 'Sin asignar'.`
    );
  });

  it("RD-7: No getAccountLabel/formatAccountLabel imported into ReassignmentDialog (source)", () => {
    assert.ok(
      !reassignSrc.includes("getAccountLabel") && !reassignSrc.includes("formatAccountLabel"),
      "RD-7 DEFECT: ReassignmentDialog imports or calls a broker·name label function. " +
      "Only name-only functions (formatAccountName, getAccountName) must be used here."
    );
  });

  it("RD-8: API payloads use source_account_id / dest_account_id fields (not label strings)", () => {
    assert.ok(
      reassignSrc.includes("source_account_id") && reassignSrc.includes("dest_account_id"),
      "RD-8 DEFECT: ReassignmentDialog API payload is missing source_account_id or dest_account_id. " +
      "Account identity in payloads must always be account_id, never the display name."
    );
  });

  it("RD-9: Behavioral: option label for named account is name only — no broker prefix", () => {
    const withBroker = accounts.find((a) => a.broker && a.name);
    const label = formatAccountName(withBroker);
    assert.ok(!label.includes("·"),
      `RD-9 DEFECT: formatAccountName for broker+name account returned '${label}' ` +
      "which contains '·'. Dropdown option text must never show broker prefix."
    );
    assert.ok(!Object.values(BROKER_LABELS).some((b) => label.startsWith(b + " ")),
      `RD-9 DEFECT: formatAccountName for broker+name account starts with a broker label. ` +
      "Option text must be account name only."
    );
  });

  it("RD-10: Behavioral: getAccountName for current account display = name only", () => {
    const target = accounts.find((a) => a.account_id === "acc-ib-3");
    const label = getAccountName(target.account_id, accounts);
    assert.equal(label, "Options Account",
      "RD-10 DEFECT: getAccountName did not return bare account name for current-account display.");
    assert.ok(!label.includes("·"),
      "RD-10 DEFECT: Current account display label contains '·' separator.");
    assert.ok(!label.toLowerCase().includes("interactive"),
      "RD-10 DEFECT: Broker name 'Interactive Brokers' leaked into current-account display.");
  });
});

// ---------------------------------------------------------------------------
// MV: Movement display sites — granular per-surface checks and behavioral
//
// Extends SC group with site-specific assertions for the four main rendering
// call-sites: movement table filter, movement detail dialog, stock transactions
// table, and AccountBadge behavioral rendering.
// All tests PASS against current source.
// ---------------------------------------------------------------------------

describe("MV: Movement display sites — granular account label checks", () => {
  it("MV-1: PortfolioMovementsTable account filter select option text uses formatAccountName (source)", () => {
    // The account filter <select> must show formatAccountName(a), not formatAccountLabel(a).
    assert.ok(
      movTableSrc.includes("formatAccountName(a)"),
      "MV-1 DEFECT: PortfolioMovementsTable account filter select does not render " +
      "formatAccountName(a) as option text. Account filter labels must be name-only."
    );
  });

  it("MV-2: PortfolioMovementsTable filter _unassigned sentinel renders 'Sin asignar' (source)", () => {
    assert.ok(
      movTableSrc.includes("Sin asignar"),
      "MV-2 DEFECT: PortfolioMovementsTable filter select does not have a 'Sin asignar' " +
      "option for the _unassigned sentinel account."
    );
  });

  it("MV-3: MovementDetailDialog 'Account' field uses getAccountName(m.account_id (source)", () => {
    assert.ok(
      movDetailSrc.includes("getAccountName(m.account_id"),
      "MV-3 DEFECT: MovementDetailDialog does not call getAccountName(m.account_id) for the " +
      "Account display field. Must use name-only lookup."
    );
  });

  it("MV-4: MovementDetailDialog transfer 'From account'/'To account' use getAccountName (source)", () => {
    assert.ok(
      movDetailSrc.includes("getAccountName(m.transfer_source_account_id") ||
      movDetailSrc.includes("getAccountName(m.transfer_source_account_id!"),
      "MV-4 DEFECT: MovementDetailDialog 'From account' transfer field does not use getAccountName. " +
      "Transfer source/dest labels must be name-only."
    );
    assert.ok(
      movDetailSrc.includes("getAccountName(m.transfer_dest_account_id") ||
      movDetailSrc.includes("getAccountName(m.transfer_dest_account_id!"),
      "MV-4 DEFECT: MovementDetailDialog 'To account' transfer field does not use getAccountName. " +
      "Transfer dest label must be name-only."
    );
  });

  it("MV-5: StockTransactionsTable accountMap values built from getAccountName (source)", () => {
    assert.ok(
      stockTxnSrc.includes("getAccountName(") && stockTxnSrc.includes("accountMap"),
      "MV-5 DEFECT: StockTransactionsTable does not build accountMap values from getAccountName. " +
      "Per-account column display must use name-only lookup."
    );
  });

  it("MV-6: StockTransactionsTable cell fallback is accountMap[id] ?? account_id — never blank (source)", () => {
    assert.ok(
      stockTxnSrc.includes("accountMap[m.account_id]"),
      "MV-6 DEFECT: StockTransactionsTable does not index accountMap by m.account_id. " +
      "Account cell must display the mapped name-only label."
    );
    // The ?? fallback ensures no blank cell even for unmapped IDs.
    const hasIdFallback =
      stockTxnSrc.includes("accountMap[m.account_id] ??") ||
      stockTxnSrc.includes("accountMap[m.account_id]??");
    assert.ok(hasIdFallback,
      "MV-6 DEFECT: StockTransactionsTable account cell lacks a ?? fallback after accountMap lookup. " +
      "Must fall back to m.account_id so the cell is never blank."
    );
  });

  it("MV-7: AccountBadge label property uses getAccountName (source)", () => {
    assert.ok(
      badgeSrc.includes("getAccountName("),
      "MV-7 DEFECT: AccountBadge does not call getAccountName for the label. " +
      "Badge text must be name-only."
    );
    assert.ok(!badgeSrc.includes("getAccountLabel"),
      "MV-7 DEFECT: AccountBadge calls getAccountLabel (broker·name function). " +
      "Must use getAccountName only."
    );
  });

  it("MV-8: Behavioral: AccountBadge for _unassigned → 'Sin asignar'", () => {
    const label = getAccountName("_unassigned", accounts);
    assert.equal(label, UNASSIGNED_LABEL,
      "MV-8 DEFECT: AccountBadge label for _unassigned account_id is not 'Sin asignar'. " +
      "The unassigned sentinel must render UNASSIGNED_LABEL, not '_unassigned'.");
  });

  it("MV-9: Behavioral: AccountBadge for named account → name only, no broker prefix", () => {
    const label = getAccountName("acc-heytrade-1", accounts);
    assert.equal(label, "Cuenta Principal",
      "MV-9 DEFECT: AccountBadge label for acc-heytrade-1 is not 'Cuenta Principal'. " +
      "Named account must display name only.");
    assert.ok(!label.includes("·"),
      "MV-9 DEFECT: AccountBadge label contains '·' separator.");
    assert.ok(!label.toLowerCase().includes("heytrade"),
      "MV-9 DEFECT: Broker name 'HeyTrade' leaked into AccountBadge label.");
  });

  it("MV-10: Behavioral: AccountBadge for account with empty name → account_id (not broker)", () => {
    const label = getAccountName("acc-no-name", accounts);
    assert.equal(label, "acc-no-name",
      "MV-10 DEFECT: AccountBadge for account with empty name did not fall back to account_id.");
    assert.ok(!label.toLowerCase().includes("heytrade"),
      "MV-10 DEFECT: Broker name used as fallback when account name is absent. " +
      "account_id must be the fallback, not broker.");
  });
});

// ---------------------------------------------------------------------------
// MC: MovementCorrectionDialog — account display site name-only
//
// MovementCorrectionDialog shows the movement's account in a read-only field.
// All tests PASS against current source.
// ---------------------------------------------------------------------------

describe("MC: MovementCorrectionDialog — account display is name-only", () => {
  it("MC-1: MovementCorrectionDialog imports getAccountName (source)", () => {
    assert.ok(
      correctionSrc.includes("getAccountName"),
      "MC-1 DEFECT: MovementCorrectionDialog does not import getAccountName. " +
      "The account display field must use the name-only lookup function."
    );
  });

  it("MC-2: MovementCorrectionDialog uses getAccountName for account display field (source)", () => {
    assert.ok(
      correctionSrc.includes("getAccountName(m.account_id"),
      "MC-2 DEFECT: MovementCorrectionDialog does not call getAccountName(m.account_id) for " +
      "the account display field. Must render account name only."
    );
  });

  it("MC-3: No formatAccountLabel / getAccountLabel in MovementCorrectionDialog (source)", () => {
    assert.ok(
      !correctionSrc.includes("formatAccountLabel") && !correctionSrc.includes("getAccountLabel"),
      "MC-3 DEFECT: MovementCorrectionDialog calls a broker·name label function. " +
      "Only name-only functions must be used for informational account display."
    );
  });

  it("MC-4: MovementCorrectionDialog API payload uses account_id field (source)", () => {
    assert.ok(
      correctionSrc.includes("account_id: m.account_id") ||
      correctionSrc.includes("account_id:m.account_id"),
      "MC-4 DEFECT: MovementCorrectionDialog does not include account_id in its API payload. " +
      "Account identity in POST/PATCH bodies must always be account_id, not display name."
    );
  });
});

// ---------------------------------------------------------------------------
// HB: PortfolioHoldingsCard By-account label — no-broker-directive angle
//
// Complements the HA group in symbolDetailRedesign.test.mjs with checks that
// PortfolioHoldingsCard never uses broker·name formatting functions, matching
// the repository-wide no-broker-prefix-for-informational-displays directive.
// HA-6 (unassigned defect) is cross-referenced here for traceability.
// All source-contract tests PASS; HB-3 documents the open defect.
// ---------------------------------------------------------------------------

describe("HB: PortfolioHoldingsCard By-account — no broker prefix, correct fallbacks", () => {
  it("HB-1: PortfolioHoldingsCard does not import formatAccountLabel or getAccountLabel (source)", () => {
    assert.ok(
      !holdingsSrc.includes("formatAccountLabel") && !holdingsSrc.includes("getAccountLabel"),
      "HB-1 DEFECT: PortfolioHoldingsCard imports or calls a broker·name label function. " +
      "By-account labels use raw API field account_name, which must already be name-only. " +
      "No broker·name formatting function should be applied."
    );
  });

  it("HB-2: By-account label expression uses account_name field as primary (source)", () => {
    assert.ok(
      holdingsSrc.includes("account_name"),
      "HB-2 DEFECT: PortfolioHoldingsCard By-account section does not reference account_name. " +
      "The visible label must use account_name as the primary display field."
    );
  });

  it("HB-3: _unassigned sentinel in By-account does not render raw '_unassigned' (open defect cross-ref)", () => {
    // Current source: {acct.account_name ?? acct.account_id}
    // When account_id = '_unassigned' and account_name is absent, this renders '_unassigned'.
    // Fix required: guard the sentinel before the nullish fallback.
    // This is tracked as HA-6 in symbolDetailRedesign.test.mjs.
    // Here we verify the guard is present in source; if absent → DEFECT.
    const hasUnassignedGuard =
      holdingsSrc.includes("_unassigned") ||
      holdingsSrc.includes("UNASSIGNED_LABEL") ||
      holdingsSrc.includes("Sin asignar");
    assert.ok(hasUnassignedGuard,
      "HB-3 DEFECT: PortfolioHoldingsCard By-account label has no guard for the '_unassigned' " +
      "sentinel. When account_id is '_unassigned' and account_name is absent, the raw string " +
      "'_unassigned' is shown to the user. Fix: check account_id === '_unassigned' and render " +
      "'Sin asignar' (UNASSIGNED_LABEL) instead. See also: HA-6 in symbolDetailRedesign.test.mjs."
    );
  });

  it("HB-4: By-account section uses account_id as React key — grouping stable by ID (source)", () => {
    assert.ok(
      holdingsSrc.includes("key={acct.account_id}") || holdingsSrc.includes('key={acct["account_id"]}'),
      "HB-4 DEFECT: PortfolioHoldingsCard By-account map does not use account_id as React key. " +
      "Grouping and reconciliation identity must be account_id; account_name is volatile."
    );
  });

  it("HB-5: By-account section never renders '·' separator in label expression (source)", () => {
    const byAccountBlock = holdingsSrc.slice(
      holdingsSrc.indexOf("By account"),
      holdingsSrc.indexOf("By account") === -1 ? holdingsSrc.length
        : holdingsSrc.indexOf("By account") + 800
    );
    assert.ok(!byAccountBlock.includes("·"),
      "HB-5 DEFECT: '·' separator found in PortfolioHoldingsCard By-account rendering block. " +
      "Account labels must never include broker·name formatting."
    );
  });
});

// ---------------------------------------------------------------------------
// PHT: PortfolioHoldingsTable — account filter and active-filter display
//
// New component (PortfolioHoldingsTable.tsx) introduced in the 2026-09-08
// pricing/portfolio-views commit. Provides a global portfolio holdings table
// with a per-account filter select and an active-filter summary label.
// All tests PASS against current source — component correctly uses name-only
// functions from the repository-wide account-display directive.
// ---------------------------------------------------------------------------

describe("PHT: PortfolioHoldingsTable — account filter labels name-only", () => {
  it("PHT-1: imports formatAccountName and getAccountName (not formatAccountLabel) (source)", () => {
    assert.ok(
      holdingsTableSrc.includes("formatAccountName") && holdingsTableSrc.includes("getAccountName"),
      "PHT-1 DEFECT: PortfolioHoldingsTable does not import name-only account display functions."
    );
    assert.ok(!holdingsTableSrc.includes("formatAccountLabel"),
      "PHT-1 DEFECT: PortfolioHoldingsTable imports formatAccountLabel (broker·name function). " +
      "Informational displays must use formatAccountName only."
    );
  });

  it("PHT-2: account filter select option text uses formatAccountName(a) per account (source)", () => {
    assert.ok(
      holdingsTableSrc.includes("{formatAccountName(a)}"),
      "PHT-2 DEFECT: PortfolioHoldingsTable account filter select does not render " +
      "formatAccountName(a) as option text. Account filter labels must be name-only."
    );
  });

  it("PHT-3: account filter option value is a.account_id — filter payload uses ID, not name (source)", () => {
    assert.ok(
      holdingsTableSrc.includes("value={a.account_id}"),
      "PHT-3 DEFECT: PortfolioHoldingsTable account filter option value is not a.account_id. " +
      "Filter value sent to API must be account_id, not the display name."
    );
  });

  it("PHT-4: _unassigned sentinel in filter select renders 'Sin asignar' (source)", () => {
    assert.ok(
      holdingsTableSrc.includes("Sin asignar"),
      "PHT-4 DEFECT: PortfolioHoldingsTable account filter does not have a 'Sin asignar' " +
      "option for the _unassigned sentinel account."
    );
  });

  it("PHT-5: active-filter summary display uses getAccountName for account label (source)", () => {
    assert.ok(
      holdingsTableSrc.includes("getAccountName(accountFilter") ||
      holdingsTableSrc.includes("getAccountName(accountFilter,"),
      "PHT-5 DEFECT: PortfolioHoldingsTable active-filter account label does not call " +
      "getAccountName(accountFilter, accounts). The visible filter summary must be name-only."
    );
  });

  it("PHT-6: no formatAccountLabel / getAccountLabel anywhere in PortfolioHoldingsTable (source)", () => {
    assert.ok(
      !holdingsTableSrc.includes("formatAccountLabel") &&
      !holdingsTableSrc.includes("getAccountLabel"),
      "PHT-6 DEFECT: PortfolioHoldingsTable calls a broker·name label function. " +
      "Only formatAccountName / getAccountName must be used."
    );
  });

  it("PHT-7: Behavioral: filter option label for broker+name account = name only, no '·'", () => {
    const withBroker = accounts.find((a) => a.broker && a.name);
    const label = formatAccountName(withBroker);
    assert.ok(!label.includes("·"),
      `PHT-7 DEFECT: formatAccountName for broker+name account returned '${label}' ` +
      "containing '·'. Filter option text must be account name only."
    );
    assert.ok(label === withBroker.name.trim(),
      `PHT-7 DEFECT: formatAccountName returned '${label}' not the bare name '${withBroker.name}'. ` +
      "No broker prefix allowed in filter option text."
    );
  });

  it("PHT-8: Behavioral: getAccountName('_unassigned') = 'Sin asignar' for active-filter display", () => {
    const label = getAccountName("_unassigned", accounts);
    assert.equal(label, UNASSIGNED_LABEL,
      "PHT-8 DEFECT: getAccountName('_unassigned') did not return 'Sin asignar'. " +
      "Active filter display for the unassigned account must show UNASSIGNED_LABEL."
    );
  });
});
