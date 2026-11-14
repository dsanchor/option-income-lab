import type { FxRateSource } from "@/types/portfolio";

/** Cash amounts use six decimal places; FX is EUR per native currency unit. */
function decimal(value: string) {
  const text = value.trim();
  if (text.length > 80 || !/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(text)) return null;
  const [whole, fraction = ""] = text.split(".");
  return {
    units: BigInt((whole || "0") + fraction),
    scale: BigInt(10) ** BigInt(fraction.length),
  };
}

function rounded(numerator: bigint, denominator: bigint, places: number): string {
  const scale = BigInt(10) ** BigInt(places);
  const units = (numerator * scale * BigInt(2) + denominator) / (denominator * BigInt(2));
  const digits = units.toString().padStart(places + 1, "0");
  return `${digits.slice(0, -places)}.${digits.slice(-places)}`;
}

export function nativeToEur(amount: string, rate: string): string {
  const a = decimal(amount);
  const r = decimal(rate);
  if (!a || !r || r.units === BigInt(0)) return "";
  return rounded(a.units * r.units, a.scale * r.scale, 6);
}

export function eurToNative(amount: string, rate: string): string | null {
  const a = decimal(amount);
  const r = decimal(rate);
  if (!a || !r || r.units === BigInt(0)) return null;
  return rounded(a.units * r.scale, a.scale * r.units, 12);
}

export function usesUsdSource(currency: string, country: string): boolean {
  return currency === "USD" && country === "US";
}

export function validFxContext(currency: string, date: string): boolean {
  if (!/^[A-Z]{3}$/.test(currency) || currency === "EUR" || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return false;
  const parsed = new Date(`${date}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === date;
}

interface CashFxState {
  currency: string;
  payment_date: string;
  fx_rate: string;
  fx_rate_source: FxRateSource;
  fx_preserve_initial: boolean;
  cd_gross: string;
  cd_gross_eur: string;
  cd_wht_src_country: string;
  cd_wht_src_amount: string;
  // null means a persisted EUR amount still needs its native equivalent.
  cd_wht_src_usd: string | null;
}

/** Update conversions atomically so previews and submission never see stale EUR. */
export function updateCashDividendFx<T extends CashFxState>(previous: T, patch: Partial<T>): T {
  const next = { ...previous, ...patch };
  const contextChanged = next.currency !== previous.currency || next.payment_date !== previous.payment_date;
  if (contextChanged) {
    next.fx_rate = "";
    next.fx_rate_source = "ECB";
    next.fx_preserve_initial = false;
  }
  const rateChanged = contextChanged || patch.fx_rate !== undefined;
  if (rateChanged || patch.cd_gross !== undefined) {
    next.cd_gross_eur = next.currency === "EUR"
      ? next.cd_gross
      : nativeToEur(next.cd_gross, next.fx_rate);
  }
  const wasUsd = usesUsdSource(previous.currency, previous.cd_wht_src_country);
  const isUsd = usesUsdSource(next.currency, next.cd_wht_src_country);
  if (!isUsd) {
    next.cd_wht_src_usd = null;
  } else if (!wasUsd || next.cd_wht_src_usd === null) {
    // Retain stored EUR exactly until a native amount or rate is actually edited.
    next.cd_wht_src_usd = eurToNative(next.cd_wht_src_amount, next.fx_rate);
  } else if (rateChanged || patch.cd_wht_src_usd !== undefined) {
    next.cd_wht_src_amount = nativeToEur(next.cd_wht_src_usd, next.fx_rate);
  }
  return next;
}

export function cashDividendFxError(form: CashFxState): string | null {
  if (!nativeToEur(form.cd_gross, "1")) return "Cash dividend gross amount must be a non-negative decimal.";
  if (form.currency !== "EUR" && (!validFxContext(form.currency, form.payment_date) || !nativeToEur(form.cd_gross, form.fx_rate) || !nativeToEur(form.cd_gross_eur, "1"))) {
    return "A valid payment-date FX rate is required to convert the cash dividend to EUR.";
  }
  if (usesUsdSource(form.currency, form.cd_wht_src_country) &&
      (form.cd_wht_src_usd === null || !nativeToEur(form.cd_wht_src_usd, form.fx_rate) || !nativeToEur(form.cd_wht_src_amount, "1"))) {
    return "Enter origin withholding in USD (use 0 for confirmed zero) and a valid FX rate.";
  }
  return null;
}
