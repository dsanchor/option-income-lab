import type {
  DividendPosition,
  DividendsReport,
  EconomicsAggregatedReport,
} from "@/types/economics";
import {
  isUnsupportedRightsMovement as isUnsupportedLegacyMovement,
  isUnsupportedRightsWarning as isUnsupportedLegacyWarning,
} from "./legacyMovementExclusion.js";

export interface RightsCompatibilityShape {
  sales_type?: string | null;
  sales_type_raw?: unknown;
  is_rights_sale?: unknown;
  source_derechos_amount?: unknown;
  source_rights_amount?: unknown;
  derechos?: unknown;
  derechos_amount?: unknown;
  rights_amount?: unknown;
  ca_leg_type?: string | null;
  ca_event_type?: string | null;
  event_type?: string | null;
  leg_type?: string | null;
  warnings?: Array<{ type?: string | null } | string> | null;
  movement_warnings?: string[] | null;
  source_row?: unknown;
  source_payload?: unknown;
}

/** Fail closed for unsupported rights records returned by older APIs/data. */
export function isUnsupportedRightsMovement(
  movement: RightsCompatibilityShape,
): boolean {
  return isUnsupportedLegacyMovement(movement);
}

export function excludeUnsupportedRightsMovements<T extends RightsCompatibilityShape>(
  movements: T[],
): T[] {
  return movements.filter((movement) => !isUnsupportedRightsMovement(movement));
}

export function isUnsupportedRightsWarning(warning: unknown): boolean {
  return isUnsupportedLegacyWarning(warning);
}

export function getCanonicalTotalNetEur(
  summary: { total_net_eur?: unknown },
): number | undefined {
  return typeof summary.total_net_eur === "number" && Number.isFinite(summary.total_net_eur)
    ? summary.total_net_eur
    : undefined;
}

function sum<T>(rows: T[], value: (row: T) => number): number {
  return rows.reduce((total, row) => total + value(row), 0);
}

function cashNet(row: { cash_net?: number | null; net_eur: number }): number {
  return Number(row.cash_net ?? row.net_eur ?? 0);
}

/**
 * Removes legacy rights-bearing dividend rows and rebuilds client-visible
 * totals from the remaining ordinary cash-dividend positions.
 */
export function excludeUnsupportedRightsFromDividends(
  report: DividendsReport,
): DividendsReport {
  const positions = report.positions.filter((position) => !isUnsupportedRightsMovement(position));
  const excluded = report.positions.filter(isUnsupportedRightsMovement);
  if (excluded.length === 0) return report;

  const removedByMonth = new Map<string, DividendPosition[]>();
  const removedBySymbol = new Map<string, DividendPosition[]>();
  const removedByYear = new Map<number, DividendPosition[]>();
  for (const position of excluded) {
    const month = position.trade_date.slice(0, 7);
    const year = Number(position.trade_date.slice(0, 4));
    removedByMonth.set(month, [...(removedByMonth.get(month) ?? []), position]);
    removedBySymbol.set(position.symbol, [...(removedBySymbol.get(position.symbol) ?? []), position]);
    removedByYear.set(year, [...(removedByYear.get(year) ?? []), position]);
  }

  const subtract = <T extends {
    gross_eur: number;
    net_eur: number;
    cash_net?: number | null;
    total_net?: number | null;
    dividend_count: number;
  }>(row: T, removed: DividendPosition[]): T => ({
    ...row,
    gross_eur: row.gross_eur - sum(removed, (item) => item.gross_eur),
    net_eur: row.net_eur - sum(removed, (item) => item.net_eur),
    cash_net: cashNet(row) - sum(removed, cashNet),
    total_net: cashNet(row) - sum(removed, cashNet),
    dividend_count: Math.max(0, row.dividend_count - removed.length),
  });

  const monthly = report.monthly
    .map((row) => subtract(row, removedByMonth.get(row.month) ?? []))
    .filter((row) => row.dividend_count > 0);
  const bySymbol = report.by_symbol
    .map((row) => subtract(row, removedBySymbol.get(row.symbol) ?? []))
    .filter((row) => row.dividend_count > 0);
  const yearly = report.yearly
    .map((row) => subtract(row, removedByYear.get(row.year) ?? []))
    .filter((row) => row.dividend_count > 0);

  let cumulative = 0;
  const cumulativeRows = report.cumulative
    .map((row) => {
      const month = monthly.find((item) => item.month === row.month);
      if (!month) return null;
      cumulative += cashNet(month);
      return {
        ...row,
        cumulative_net_eur: cumulative,
        cash_net: cumulative,
        total_net: cumulative,
        cumulative_cash_net_eur: cumulative,
        cumulative_total_net_eur: cumulative,
      };
    })
    .filter((row): row is NonNullable<typeof row> => row !== null);

  const totalGross = sum(positions, (position) => position.gross_eur);
  const totalFees = sum(positions, (position) => position.fees_eur);
  const totalWithholding = sum(positions, (position) => position.withholding_total_eur);
  const totalNet = sum(positions, cashNet);

  return {
    ...report,
    summary: {
      ...report.summary,
      total_gross_eur: totalGross,
      total_fees_eur: totalFees,
      total_withholding_eur: totalWithholding,
      total_net_eur: totalNet,
      cash_net: totalNet,
      total_net: totalNet,
      total_dividends: positions.length,
      total_accounts: new Set(positions.map((position) => position.account_id).filter(Boolean)).size,
      effective_withholding_pct: totalGross > 0 ? (totalWithholding / totalGross) * 100 : 0,
    },
    monthly,
    by_symbol: bySymbol,
    yearly,
    cumulative: cumulativeRows,
    positions,
    filters: {
      ...report.filters,
      symbols: Array.from(new Set(positions.map((position) => position.symbol))).sort(),
      account_ids: Array.from(
        new Set(positions.map((position) => position.account_id).filter((id): id is string => Boolean(id))),
      ).sort(),
    },
  };
}

/** Uses cash-only dividend values when an older overview still includes rights. */
export function excludeUnsupportedRightsFromEconomicsOverview(
  report: EconomicsAggregatedReport,
): EconomicsAggregatedReport {
  const summaryDividendNet = report.summary.dividends_cash_net_eur ?? report.summary.dividends_net_eur;
  return {
    ...report,
    summary: {
      ...report.summary,
      dividends_net_eur: summaryDividendNet,
      dividends_total_net_eur: summaryDividendNet,
      combined_net_eur: report.summary.options_net_eur + summaryDividendNet,
    },
    monthly: report.monthly.map((row) => {
      const dividendNet = row.dividends_cash_net_eur ?? row.dividends_net_eur;
      return {
        ...row,
        dividends_net_eur: dividendNet,
        dividends_total_net_eur: dividendNet,
        combined_net_eur: row.options_net_eur + dividendNet,
      };
    }),
    by_symbol: report.by_symbol.map((row) => {
      const dividendNet = row.dividends_cash_net_eur ?? row.dividends_net_eur;
      return {
        ...row,
        dividends_net_eur: dividendNet,
        dividends_total_net_eur: dividendNet,
        combined_net_eur: row.options_net_eur + dividendNet,
      };
    }),
  };
}
