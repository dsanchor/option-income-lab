export type HoldingPnlTone = "positive" | "negative" | "neutral";

export interface HoldingPnlDisplay {
  absolute: string;
  percentage: string;
  tone: HoldingPnlTone;
  ariaLabel: string;
  available: boolean;
}

interface HoldingPnlFields {
  currentShares: string | null | undefined;
  unrealizedPnlEur: string | null | undefined;
  unrealizedPnlPct: string | null | undefined;
}

const EUR_FORMATTER = new Intl.NumberFormat("de-DE", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 2,
  signDisplay: "exceptZero",
});

function decimal(value: string | null | undefined): number | null {
  if (value == null || value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function formatHoldingPnl({
  currentShares,
  unrealizedPnlEur,
  unrealizedPnlPct,
}: HoldingPnlFields): HoldingPnlDisplay {
  const shares = decimal(currentShares);
  const pnl = decimal(unrealizedPnlEur);

  if (shares == null || shares <= 0 || pnl == null) {
    return {
      absolute: "—",
      percentage: "—",
      tone: "neutral",
      ariaLabel: "Unrealized P/L unavailable",
      available: false,
    };
  }

  const percentage = decimal(unrealizedPnlPct);
  const tone = pnl > 0 ? "positive" : pnl < 0 ? "negative" : "neutral";
  const status = pnl > 0 ? "Unrealized gain" : pnl < 0 ? "Unrealized loss" : "No unrealized gain or loss";
  const absolute = EUR_FORMATTER.format(pnl);
  const percentageText =
    percentage == null
      ? "—"
      : `${percentage > 0 ? "+" : ""}${Object.is(percentage, -0) ? 0 : percentage.toFixed(2)}%`;

  return {
    absolute,
    percentage: percentageText,
    tone,
    ariaLabel: `${status}: ${absolute}; ${
      percentage == null ? "percentage unavailable" : percentageText
    }`,
    available: true,
  };
}
