import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {
  excludeUnsupportedRightsFromDividends,
  excludeUnsupportedRightsFromEconomicsOverview,
  excludeUnsupportedRightsMovements,
  getCanonicalTotalNetEur,
  isUnsupportedRightsMovement,
  isUnsupportedRightsWarning,
} from "../src/lib/rightsExclusion.ts";

describe("legacy unsupported movement exclusion", () => {
  it("excludes every legacy marker while preserving ordinary sales and Dividend · Buy", () => {
    const ordinarySell = { id: "sell", txn_type: "SELL", sales_type: "ACCIONES" };
    const dividendBuy = {
      id: "scrip",
      txn_type: "BUY",
      ca_leg_type: "SHARE_ACQUISITION",
      ca_event_type: "SCRIP_DIVIDEND",
    };
    const unsupported = [
      { id: "sale", txn_type: "SELL", sales_type: "  derechos " },
      { id: "flag", txn_type: "SELL", is_rights_sale: true },
      { id: "leg", txn_type: "SELL", ca_leg_type: " rights_sold " },
      { id: "event", txn_type: "BUY", ca_event_type: "rights_issue" },
      { id: "amount", txn_type: "DIVIDEND", source_derechos_amount: "2.50" },
      { id: "warning", txn_type: "DIVIDEND", warnings: [{ type: " rights_amount " }] },
    ];

    assert.deepEqual(
      excludeUnsupportedRightsMovements([ordinarySell, dividendBuy, ...unsupported]).map((row) => row.id),
      ["sell", "scrip"],
    );
    assert.equal(isUnsupportedRightsMovement({ source_derechos_amount: "0" }), false);
  });

  it("fails closed for malformed amounts, aliases, nested source data, and unknown sale markers", () => {
    const unsupported = [
      { source_derechos_amount: "not-a-number" },
      { source_rights_amount: Number.NaN },
      { derechos_amount: Number.POSITIVE_INFINITY },
      { rights_amount: {} },
      { derechos: [] },
      { sales_type: " BONOS " },
      { sales_type: 7 },
      { sales_type_raw: "unknown" },
      { is_rights_sale: "maybe" },
      { source_row: { " Importe en Derechos ": " 1,50 " } },
      { source_payload: { raw: { rights_amount: "broken" } } },
      { movement_warnings: [" derechos_with_quantity "] },
    ];
    for (const movement of unsupported) {
      assert.equal(isUnsupportedRightsMovement(movement), true);
    }
  });

  it("normalizes accents, punctuation, separators, whitespace, and nested marker values", () => {
    const unsupported = [
      { sales_type: "  dérêchos  " },
      { ca_leg_type: "RIGHTS-SOLD" },
      { ca_leg_type: "rights.sold" },
      { ca_leg_type: "rights_sold" },
      { ca_leg_type: " rights   sold " },
      { source_payload: { raw: { " importe--en.dérechos ": "1,00" } } },
      { source_data: [{ sales_type_raw: "  RÍGHTS / SOLD " }] },
      { raw_source: { nested: { is_derechos_sale: " sí " } } },
    ];
    for (const movement of unsupported) {
      assert.equal(isUnsupportedRightsMovement(movement), true);
    }
  });

  it("does not infer rights movements from ordinary descriptions or company names", () => {
    const ordinary = [
      { txn_type: "BUY", description: "Purchased after reading the rights offering notice" },
      { txn_type: "SELL", company_name: "Human Rights Watch Holdings", sales_type: "shares" },
      { txn_type: "DIVIDEND", source_payload: { description: "copyrights income" } },
      { txn_type: "SELL", metadata: { note: "all rights reserved" }, sales_type: "stock" },
    ];
    for (const movement of ordinary) {
      assert.equal(isUnsupportedRightsMovement(movement), false);
    }
  });

  it("accepts explicit zero amounts and ordinary markers without weakening rights warnings", () => {
    for (const movement of [
      { source_derechos_amount: 0 },
      { source_rights_amount: " 0.00 " },
      { source_row: { "Importe en Derechos": "0,00" } },
      { sales_type: " acciones ", is_rights_sale: false },
      { txn_type: "SELL" },
      { txn_type: "DIVIDEND", ca_leg_type: " cash_dividend " },
    ]) {
      assert.equal(isUnsupportedRightsMovement(movement), false);
    }
    assert.equal(isUnsupportedRightsWarning({ type: " rights_amount " }), true);
    assert.equal(isUnsupportedRightsWarning(" Derechos_with_quantity "), true);
  });

  it("removes legacy dividend rows and their totals", () => {
    const report = {
      summary: {
        total_gross_eur: 130,
        total_fees_eur: 3,
        total_withholding_eur: 13,
        total_net_eur: 139,
        cash_net: 114,
        derechos_net: 25,
        total_net: 139,
        effective_withholding_pct: 10,
        total_dividends: 2,
        total_accounts: 1,
      },
      monthly: [{
        month: "2026-09", gross_eur: 130, fees_eur: 3,
        withholding_source_eur: 13, withholding_destination_eur: 0,
        withholding_total_eur: 13, net_eur: 139, cash_net: 114,
        derechos_net: 25, total_net: 139, dividend_count: 2,
      }],
      by_symbol: [{
        symbol: "ACME", gross_eur: 130, withholding_total_eur: 13,
        net_eur: 139, cash_net: 114, derechos_net: 25, total_net: 139,
        dividend_count: 2,
      }],
      yearly: [{
        year: 2026, gross_eur: 130, withholding_eur: 13, net_eur: 139,
        cash_net: 114, derechos_net: 25, total_net: 139, dividend_count: 2,
      }],
      cumulative: [{ month: "2026-09", cumulative_net_eur: 139 }],
      positions: [
        {
          id: "cash", account_id: "a", security_id: "x", symbol: "ACME",
          trade_date: "2026-09-01", gross_amount: 100, gross_currency: "EUR",
          gross_eur: 100, fees_eur: 1, withholding_source_eur: 10,
          withholding_destination_eur: 0, withholding_total_eur: 10,
          net_eur: 89, cash_net: 89, total_net: 89, correction_status: "ACTIVE",
        },
        {
          id: "legacy", account_id: "a", security_id: "x", symbol: "ACME",
          trade_date: "2026-09-02", gross_amount: 30, gross_currency: "EUR",
          gross_eur: 30, fees_eur: 2, withholding_source_eur: 3,
          withholding_destination_eur: 0, withholding_total_eur: 3,
          net_eur: 50, cash_net: 25, derechos_net: " malformed ", total_net: 50,
          correction_status: "ACTIVE",
        },
      ],
      filters: { years: [2026], symbols: ["ACME"], account_ids: ["a"] },
      applied_filters: { year: null, months: null, symbols: null, account_ids: null },
      meta: { bucket_field: "trade_date", value_field: "net_eur" },
    };

    const clean = excludeUnsupportedRightsFromDividends(report);
    assert.deepEqual(clean.positions.map((row) => row.id), ["cash"]);
    assert.equal(clean.summary.total_net_eur, 89);
    assert.equal(clean.summary.total_dividends, 1);
    assert.equal(clean.monthly[0].total_net, 89);
    assert.equal(clean.monthly[0].dividend_count, 1);
  });

  it("uses cash-only economics values for every aggregate and chart row", () => {
    const report = {
      summary: {
        options_net_eur: 40,
        dividends_net_eur: 125,
        dividends_cash_net_eur: 100,
        dividends_derechos_net_eur: 25,
        dividends_total_net_eur: 125,
        combined_net_eur: 165,
      },
      monthly: [{
        month: "2026-09",
        options_net_eur: 10,
        dividends_net_eur: 75,
        dividends_cash_net_eur: 60,
        dividends_derechos_net_eur: 15,
        dividends_total_net_eur: 75,
        combined_net_eur: 85,
      }],
      by_symbol: [{
        symbol: "ACME",
        options_net_eur: 30,
        dividends_net_eur: 50,
        dividends_cash_net_eur: 40,
        dividends_derechos_net_eur: 10,
        dividends_total_net_eur: 50,
        combined_net_eur: 80,
      }],
    };

    const clean = excludeUnsupportedRightsFromEconomicsOverview(report);
    assert.equal(clean.summary.dividends_net_eur, 100);
    assert.equal(clean.summary.dividends_total_net_eur, 100);
    assert.equal(clean.summary.combined_net_eur, 140);
    assert.equal(clean.monthly[0].dividends_total_net_eur, 60);
    assert.equal(clean.monthly[0].combined_net_eur, 70);
    assert.equal(clean.by_symbol[0].dividends_total_net_eur, 40);
    assert.equal(clean.by_symbol[0].combined_net_eur, 70);
  });
});

describe("creation and import UX", () => {
  for (const file of ["AddMovementDialog.tsx", "CorporateActionForm.tsx"]) {
    it(`${file} exposes no unsupported creation option`, () => {
      const source = fs.readFileSync(new URL(`../src/components/${file}`, import.meta.url), "utf8");
      assert.doesNotMatch(source, /DERECHOS|RIGHTS_ISSUE|RIGHTS_SOLD|Rights Issue|Rights sale/);
    });
  }

  it("import picker/help has no unsupported format guidance", () => {
    const source = fs.readFileSync(new URL("../src/components/ImportChat.tsx", import.meta.url), "utf8");
    assert.doesNotMatch(source, /DERECHOS|RIGHTS_AMOUNT|Rights amount|Rights sale/);
  });
});

describe("Economics dividends summary", () => {
  it("renders Total Dividends from the canonical net field without rights or cash fallback", () => {
    const source = fs.readFileSync(
      new URL("../src/components/DividendsView.tsx", import.meta.url),
      "utf8",
    );
    const summaryStart = source.indexOf("function SummaryRow");
    const totalCardStart = source.indexOf("<Reveal index={0}", summaryStart);
    const summary = source.slice(totalCardStart, source.indexOf("</Reveal>", totalCardStart));

    assert.match(source, /const netDividendsReceived = getNetDividendsReceived\(summary\)/);
    assert.match(source, /return getCanonicalTotalNetEur\(summary\)/);
    assert.doesNotMatch(source, /summary\.total_net_eur\s*\?\?/);
    assert.match(summary, /aria-labelledby="total-dividends-label"/);
    assert.match(summary, /aria-label=\{netDividendsReceived == null/);
    assert.match(summary, /netDividendsReceived == null \? "—" : eur\(netDividendsReceived\)/);
    assert.equal((summary.match(/Total Dividends/g) ?? []).length, 1);
    assert.doesNotMatch(summary, /Cash Net|Rights/);
  });

  it("keeps missing or nonfinite canonical totals unavailable instead of using cash_net", () => {
    const source = fs.readFileSync(
      new URL("../src/components/DividendsView.tsx", import.meta.url),
      "utf8",
    );
    assert.match(source, /Net dividends received unavailable/);
    assert.match(source, /netDividendsReceived == null \? "—"/);
    assert.doesNotMatch(source, /total_net_eur\s*\?\?\s*summary\.cash_net/);
    assert.equal(getCanonicalTotalNetEur({ total_net_eur: 12, cash_net: 999 }), 12);
    assert.equal(getCanonicalTotalNetEur({ cash_net: 999 }), undefined);
    assert.equal(getCanonicalTotalNetEur({ total_net_eur: Number.NaN, cash_net: 999 }), undefined);
    assert.equal(getCanonicalTotalNetEur({ total_net_eur: Number.POSITIVE_INFINITY }), undefined);
  });
});

describe("removed warning constants", () => {
  const files = [
    "../src/types/portfolio.ts",
    "../src/components/PortfolioMovementsTable.tsx",
    "../src/components/PortfolioHoldingsTable.tsx",
    "../src/components/MovementDetailDialog.tsx",
    "../src/components/ImportPreview.tsx",
  ];

  it("contains no orphan sale warning types or labels", () => {
    const obsolete = [
      ["ACCIONES", "ZERO", "QUANTITY"].join("_"),
      ["INVALID", "SALES", "TYPE"].join("_"),
    ];
    for (const path of files) {
      const source = fs.readFileSync(new URL(path, import.meta.url), "utf8");
      for (const warningType of obsolete) assert.equal(source.includes(warningType), false);
    }
  });
});

describe("all frontend read surfaces share the exclusion boundary", () => {
  const contracts = [
    ["../src/lib/portfolio-api.ts", /excludeUnsupportedRightsMovements\(response\.movements\)/],
    ["../src/lib/filterMovementsByType.ts", /isUnsupportedRightsMovement\(movement\)/],
    ["../src/components/ImportPreview.tsx", /excludeUnsupportedRightsMovements\(preview\.movements\)/],
    ["../src/components/SymbolMovementsTable.tsx", /excludeUnsupportedRightsMovements\(movements\)/],
    ["../src/components/DividendsView.tsx", /excludeUnsupportedRightsFromDividends/],
    ["../src/components/EconomicsOverviewView.tsx", /excludeUnsupportedRightsFromEconomicsOverview/],
  ];

  for (const [path, expected] of contracts) {
    it(`${path} is gated by the shared rights exclusion`, () => {
      const source = fs.readFileSync(new URL(path, import.meta.url), "utf8");
      assert.match(source, expected);
    });
  }

  it("locally corrected movement counts subtract excluded rows", () => {
    const api = fs.readFileSync(new URL("../src/lib/portfolio-api.ts", import.meta.url), "utf8");
    const symbol = fs.readFileSync(
      new URL("../src/components/SymbolMovementsTable.tsx", import.meta.url),
      "utf8",
    );
    assert.match(api, /response\.total_count - \(response\.movements\.length - movements\.length\)/);
    assert.match(symbol, /movementCount - \(movements\.length - visibleMovements\.length\)/);
  });
});
