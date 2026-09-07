import type { BrokerAccount } from "@/types/portfolio";
import { BROKER_LABELS } from "@/types/portfolio";

/** Human-readable label for the _unassigned sentinel. */
export const UNASSIGNED_LABEL = "Sin asignar";

/**
 * Format a BrokerAccount into a readable "Broker · Name" label.
 *
 * Handles partial data gracefully:
 *   broker + name  → "HeyTrade · My Account"
 *   name only      → "My Account"
 *   broker only    → "HeyTrade"
 *   neither        → "—"
 */
export function formatAccountLabel(account: BrokerAccount | null | undefined): string {
  if (!account) return "—";
  const brokerLabel =
    account.broker ? (BROKER_LABELS[account.broker] ?? account.broker) : null;
  const name = account.name?.trim() || null;
  if (brokerLabel && name) return `${brokerLabel} · ${name}`;
  if (name) return name;
  if (brokerLabel) return brokerLabel;
  return "—";
}

/**
 * Look up an account by ID in the accounts array and return a readable label.
 *
 * - "_unassigned" → UNASSIGNED_LABEL ("Sin asignar")
 * - Found account → "Broker · Name" via formatAccountLabel
 * - Not found     → raw accountId (fallback, never empty)
 */
export function getAccountLabel(accountId: string, accounts: BrokerAccount[]): string {
  if (!accountId || accountId === "_unassigned") return UNASSIGNED_LABEL;
  const account = accounts.find((a) => a.account_id === accountId);
  if (account) return formatAccountLabel(account);
  return accountId;
}

/**
 * Format an account for informational display: account name only, no broker prefix.
 * Use this everywhere a visible account label appears (tables, badges, summaries,
 * selectors, dialogs). Reserve formatAccountLabel for account-metadata management UIs.
 *
 * Falls back gracefully:
 *   name present        → trimmed name
 *   name absent         → raw account_id (last resort, never broker/type)
 *   null/undefined      → "—"
 */
export function formatAccountName(account: BrokerAccount | null | undefined): string {
  if (!account) return "—";
  const name = account.name?.trim() || null;
  if (name) return name;
  return account.account_id || "—";
}

/**
 * Name-only lookup for informational account labels.
 *
 * - "_unassigned" → UNASSIGNED_LABEL ("Sin asignar")
 * - Found account → account name only via formatAccountName
 * - Not found (legacy) → raw accountId (last resort, never blank)
 */
export function getAccountName(accountId: string, accounts: BrokerAccount[]): string {
  if (!accountId || accountId === "_unassigned") return UNASSIGNED_LABEL;
  const account = accounts.find((a) => a.account_id === accountId);
  if (account) return formatAccountName(account);
  return accountId;
}

/** @deprecated Use formatAccountName for informational display. */
export const formatMovementAccountLabel = formatAccountName;
/** @deprecated Use getAccountName for informational display. */
export const getMovementAccountLabel = getAccountName;

// ── Deterministic account badge colors ────────────────────────────────────────

/**
 * Accessible badge palette indexed deterministically by account_id.
 * Six named color slots from the design token set; each entry is a Tailwind
 * class string pairing a background tint with its matching text color.
 */
export const ACCOUNT_BADGE_PALETTE = [
  "bg-accent-blue/15 text-accent-blue",
  "bg-accent-green/15 text-accent-green",
  "bg-accent-purple/15 text-accent-purple",
  "bg-accent-cyan/15 text-accent-cyan",
  "bg-accent-orange/15 text-accent-orange",
  "bg-accent-red/15 text-accent-red",
] as const;

/** Neutral badge class used for the _unassigned sentinel. */
export const UNASSIGNED_BADGE_CLASS = "bg-bg-hover text-text-muted";

/**
 * Deterministic palette index for an account_id.
 * Returns -1 for the _unassigned sentinel (caller should use UNASSIGNED_BADGE_CLASS).
 * The same ID always maps to the same index across all renders and page loads.
 */
export function accountColorIndex(accountId: string): number {
  if (!accountId || accountId === "_unassigned") return -1;
  let h = 0;
  for (let i = 0; i < accountId.length; i++) {
    h = (Math.imul(31, h) + accountId.charCodeAt(i)) | 0;
  }
  return Math.abs(h) % ACCOUNT_BADGE_PALETTE.length;
}

/** Returns the Tailwind class string for the account's deterministic badge color. */
export function getAccountBadgeClass(accountId: string): string {
  if (!accountId || accountId === "_unassigned") return UNASSIGNED_BADGE_CLASS;
  return ACCOUNT_BADGE_PALETTE[accountColorIndex(accountId)];
}
