"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { getLinkablePositions } from "@/lib/portfolio-api";
import type { LinkablePosition, OptionTxnType } from "@/types/portfolio";

const inputCls =
  "w-full rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none";
const labelCls = "mb-1 block text-xs font-medium text-text-muted";

function formatPositionLabel(p: LinkablePosition): string {
  const parts: string[] = [p.type.toUpperCase()];
  if (p.strike != null) parts.push(String(p.strike));
  if (p.expiration) parts.push(`exp ${p.expiration}`);
  parts.push(`· ${p.status}`);
  if (p.close_reason) parts.push(`(${p.close_reason})`);
  if (p.closed_at) parts.push(`· closed ${p.closed_at}`);
  else if (p.opened_at) parts.push(`· opened ${p.opened_at}`);
  return `${parts.join(" ")} — ${p.position_id}`;
}

interface OptionPositionLinkPickerProps {
  /** Bare ticker symbol (not MIC:TICKER security_id). Picker is disabled until this is set. */
  symbol: string | null;
  txnType: OptionTxnType;
  value: string;
  onChange: (positionId: string) => void;
}

/**
 * Dropdown for linking an option movement to an existing option position, without
 * copy-pasting position IDs. Positions are loaded on demand (via the "Load" button),
 * filtered server-side to those eligible for the given movement's txn_type:
 * - *_SELL: positions of matching type without an already-linked opening-sell movement.
 * - *_BUY: positions of matching type that require a closing buy (rolled/manual close)
 *   without an already-linked closing-buy movement.
 * A free-text fallback input remains available for edge cases the picker can't cover.
 */
export default function OptionPositionLinkPicker({ symbol, txnType, value, onChange }: OptionPositionLinkPickerProps) {
  const [positions, setPositions] = useState<LinkablePosition[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleLoad() {
    if (!symbol) {
      setError("Select a symbol first.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await getLinkablePositions(symbol, txnType);
      setPositions(res.positions);
    } catch (err) {
      const e = err as { data?: { detail?: string } };
      setError(e.data?.detail ?? (err instanceof Error ? err.message : "Could not load positions."));
      setPositions(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="sm:col-span-2 space-y-2">
      <label className={labelCls}>Position ID</label>
      <div className="flex gap-2">
        <select
          value={positions?.some((p) => p.position_id === value) ? value : ""}
          onChange={(e) => onChange(e.target.value)}
          className={inputCls}
          disabled={!positions || positions.length === 0}
        >
          <option value="">
            {positions === null ? "Load positions to select…" : positions.length === 0 ? "No eligible positions found" : "Select a position…"}
          </option>
          {positions?.map((p) => (
            <option key={p.position_id} value={p.position_id}>
              {formatPositionLabel(p)}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={handleLoad}
          disabled={loading || !symbol}
          className="flex shrink-0 items-center gap-1 rounded-[var(--radius)] border border-border bg-bg-hover px-3 py-2 text-sm text-text-muted hover:text-text disabled:opacity-50"
          title={symbol ? "Load eligible positions for this symbol" : "Select a symbol first"}
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          {positions === null ? "Load" : "Refresh"}
        </button>
      </div>
      {error && <div className="text-xs text-accent-red">{error}</div>}
      <div>
        <label className={labelCls}>Or enter manually</label>
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="position_id"
          className={inputCls}
        />
      </div>
      <div className="text-xs text-text-muted">
        Optional. Click Load to see eligible positions for this symbol, or type the position_id directly.
      </div>
    </div>
  );
}
