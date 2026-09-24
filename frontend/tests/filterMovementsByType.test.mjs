/**
 * Tests for filterMovementsByType.ts — Stocks tab movement type filter.
 *
 * Run with: node --test frontend/tests/filterMovementsByType.test.mjs
 *
 * Imports the production helper so metadata membership cannot drift.
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {
  filterMovementsByType,
  filterMovementsForStocksTab,
  getServerMovementTypeFilter,
  isStocksTabMovement,
  matchesMovementTypeFilter,
} from "../src/lib/filterMovementsByType.ts";

describe("user-facing movement type membership", () => {
  const ordinaryBuy = { txn_type: "BUY" };
  const cashDividend = { txn_type: "DIVIDEND", ca_leg_type: "CASH_DIVIDEND" };
  const scripBuy = {
    txn_type: "BUY",
    ca_leg_type: "SHARE_ACQUISITION",
    ca_event_type: "SCRIP_DIVIDEND",
  };
  const mixedScripBuy = {
    txn_type: "BUY",
    ca_leg_type: "SHARE_ACQUISITION",
    ca_event_type: "DIVIDEND_WITH_SCRIP",
  };
  const rightsBuy = {
    txn_type: "BUY",
    ca_leg_type: "SHARE_ACQUISITION",
    ca_event_type: "RIGHTS_ISSUE",
  };
  const sell = { txn_type: "SELL" };

  it("keeps dividend-derived share acquisitions in both Buy and Dividend", () => {
    for (const movement of [scripBuy, mixedScripBuy]) {
      assert.equal(matchesMovementTypeFilter(movement, "BUY"), true);
      assert.equal(matchesMovementTypeFilter(movement, "DIVIDEND"), true);
    }
  });

  it("keeps ordinary and metadata-free buys in Buy only", () => {
    for (const movement of [ordinaryBuy, { txn_type: "BUY", ca_leg_type: undefined }]) {
      assert.equal(matchesMovementTypeFilter(movement, "BUY"), true);
      assert.equal(matchesMovementTypeFilter(movement, "DIVIDEND"), false);
    }
  });

  it("keeps cash dividends in Dividend only", () => {
    assert.equal(matchesMovementTypeFilter(cashDividend, "DIVIDEND"), true);
    assert.equal(matchesMovementTypeFilter(cashDividend, "BUY"), false);
  });

  it("keeps rights-issue share acquisitions in Buy only", () => {
    assert.equal(matchesMovementTypeFilter(rightsBuy, "BUY"), true);
    assert.equal(matchesMovementTypeFilter(rightsBuy, "DIVIDEND"), false);
  });

  it("leaves SELL and unrelated types unaffected", () => {
    assert.equal(matchesMovementTypeFilter(sell, "SELL"), true);
    assert.equal(matchesMovementTypeFilter(sell, "BUY"), false);
    assert.equal(matchesMovementTypeFilter(sell, "DIVIDEND"), false);
  });

  it("filters a mixed collection with the same dual-membership rule", () => {
    const movements = [
      ordinaryBuy,
      cashDividend,
      scripBuy,
      mixedScripBuy,
      rightsBuy,
      sell,
    ];
    assert.deepEqual(filterMovementsByType(movements, "BUY"), [
      ordinaryBuy,
      scripBuy,
      mixedScripBuy,
      rightsBuy,
    ]);
    assert.deepEqual(filterMovementsByType(movements, "DIVIDEND"), [
      cashDividend,
      scripBuy,
      mixedScripBuy,
    ]);
  });

  it("omits only Dividend from the exact backend txn_type query", () => {
    assert.equal(getServerMovementTypeFilter("BUY"), "BUY");
    assert.equal(getServerMovementTypeFilter("SELL"), "SELL");
    assert.equal(getServerMovementTypeFilter("DIVIDEND"), undefined);
    assert.equal(getServerMovementTypeFilter("ALL"), undefined);
  });
});

describe("Dividend filter surfaces use shared semantic filtering", () => {
  for (const component of [
    "PortfolioMovementsTable.tsx",
    "StockTransactionsTable.tsx",
  ]) {
    it(`${component} uses the shared helper and client-filters Dividend`, () => {
      const source = fs.readFileSync(
        new URL(`../src/components/${component}`, import.meta.url),
        "utf8",
      );
      assert.match(source, /filterMovementsByType/);
      assert.match(source, /getServerMovementTypeFilter/);
      assert.match(source, /filterMovementsByType\([^;]+(?:filter\.txn_type|tf)\)/s);
    });
  }
});

// ---------------------------------------------------------------------------
// isStocksTabMovement predicate
// ---------------------------------------------------------------------------

describe("isStocksTabMovement", () => {
  it("returns true for BUY", () => {
    assert.equal(isStocksTabMovement("BUY"), true);
  });

  it("returns true for SELL", () => {
    assert.equal(isStocksTabMovement("SELL"), true);
  });

  it("returns true for DIVIDEND", () => {
    assert.equal(isStocksTabMovement("DIVIDEND"), true);
  });

  it("returns false for TRANSFER_IN", () => {
    assert.equal(isStocksTabMovement("TRANSFER_IN"), false,
      "TRANSFER_IN must be excluded from Stocks tab"
    );
  });

  it("returns false for TRANSFER_OUT", () => {
    assert.equal(isStocksTabMovement("TRANSFER_OUT"), false,
      "TRANSFER_OUT must be excluded from Stocks tab"
    );
  });

  it("returns false for unknown type", () => {
    assert.equal(isStocksTabMovement("MYSTERY"), false);
  });

  it("returns false for empty string", () => {
    assert.equal(isStocksTabMovement(""), false);
  });

  it("is case-sensitive (lowercase buy is excluded)", () => {
    assert.equal(isStocksTabMovement("buy"), false,
      "filter must be case-sensitive — txn_type is always uppercase"
    );
  });
});

// ---------------------------------------------------------------------------
// filterMovementsForStocksTab array filter
// ---------------------------------------------------------------------------

describe("filterMovementsForStocksTab", () => {
  it("empty array returns empty array", () => {
    assert.deepEqual(filterMovementsForStocksTab([]), []);
  });

  it("BUY movement passes through", () => {
    const movs = [{ id: "m1", txn_type: "BUY", quantity: "100" }];
    const result = filterMovementsForStocksTab(movs);
    assert.equal(result.length, 1);
    assert.equal(result[0].id, "m1");
  });

  it("SELL movement passes through", () => {
    const movs = [{ id: "m2", txn_type: "SELL", quantity: "50" }];
    const result = filterMovementsForStocksTab(movs);
    assert.equal(result.length, 1);
    assert.equal(result[0].txn_type, "SELL");
  });

  it("DIVIDEND movement passes through", () => {
    const movs = [{ id: "m3", txn_type: "DIVIDEND" }];
    const result = filterMovementsForStocksTab(movs);
    assert.equal(result.length, 1);
  });

  it("TRANSFER_IN is filtered out", () => {
    const movs = [{ id: "xfer1", txn_type: "TRANSFER_IN", quantity: "25" }];
    const result = filterMovementsForStocksTab(movs);
    assert.equal(result.length, 0,
      "TRANSFER_IN must not appear in the Stocks tab"
    );
  });

  it("TRANSFER_OUT is filtered out", () => {
    const movs = [{ id: "xfer2", txn_type: "TRANSFER_OUT", quantity: "25" }];
    const result = filterMovementsForStocksTab(movs);
    assert.equal(result.length, 0,
      "TRANSFER_OUT must not appear in the Stocks tab"
    );
  });

  it("mixed array keeps only BUY/SELL/DIVIDEND", () => {
    const movs = [
      { id: "a", txn_type: "BUY" },
      { id: "b", txn_type: "TRANSFER_IN" },
      { id: "c", txn_type: "SELL" },
      { id: "d", txn_type: "TRANSFER_OUT" },
      { id: "e", txn_type: "DIVIDEND" },
    ];
    const result = filterMovementsForStocksTab(movs);
    assert.equal(result.length, 3);
    assert.deepEqual(result.map((m) => m.id), ["a", "c", "e"]);
  });

  it("preserves original order (newest-first by trade_date)", () => {
    const movs = [
      { id: "newest", txn_type: "SELL", trade_date: "2024-09-20" },
      { id: "middle", txn_type: "DIVIDEND", trade_date: "2024-06-01" },
      { id: "oldest", txn_type: "BUY", trade_date: "2024-03-15" },
    ];
    const result = filterMovementsForStocksTab(movs);
    assert.deepEqual(result.map((m) => m.id), ["newest", "middle", "oldest"]);
  });

  it("all-TRANSFER array returns empty", () => {
    const movs = [
      { id: "t1", txn_type: "TRANSFER_IN" },
      { id: "t2", txn_type: "TRANSFER_OUT" },
    ];
    assert.equal(filterMovementsForStocksTab(movs).length, 0);
  });

  it("unknown txn_type is excluded", () => {
    const movs = [{ id: "u1", txn_type: "MYSTERY" }];
    assert.equal(filterMovementsForStocksTab(movs).length, 0);
  });

  it("does not mutate input array", () => {
    const movs = [
      { id: "buy1", txn_type: "BUY" },
      { id: "xfer", txn_type: "TRANSFER_IN" },
    ];
    const original = [...movs];
    filterMovementsForStocksTab(movs);
    assert.deepEqual(movs, original, "filterMovementsForStocksTab must not mutate input");
  });

  it("all-BUY/SELL/DIVIDEND array passes through unchanged", () => {
    const movs = [
      { id: "b1", txn_type: "BUY" },
      { id: "s1", txn_type: "SELL" },
      { id: "d1", txn_type: "DIVIDEND" },
    ];
    const result = filterMovementsForStocksTab(movs);
    assert.equal(result.length, 3);
    assert.deepEqual(result.map((m) => m.id), ["b1", "s1", "d1"]);
  });

  it("SELL with sales_type ACCIONES passes through with field intact", () => {
    const movs = [{ id: "sell_shares", txn_type: "SELL", sales_type: "ACCIONES" }];
    const result = filterMovementsForStocksTab(movs);
    assert.equal(result.length, 1);
    assert.equal(result[0].sales_type, "ACCIONES");
  });

  it("SELL with sales_type DERECHOS passes through with field intact", () => {
    const movs = [{ id: "sell_rights", txn_type: "SELL", sales_type: "DERECHOS" }];
    const result = filterMovementsForStocksTab(movs);
    assert.equal(result.length, 1);
    assert.equal(result[0].sales_type, "DERECHOS");
  });
});
