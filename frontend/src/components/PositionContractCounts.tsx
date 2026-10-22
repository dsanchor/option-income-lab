import type { Position } from "@/types/symbol-detail";

export default function PositionContractCounts({ position }: { position: Position }) {
  return (
    <span className="inline-flex flex-col gap-1 text-xs">
      {(["total", "remaining"] as const).map((kind) => {
        const value = position.contract_counts?.[kind];
        const resolved = value?.status === "resolved";
        const warnings = value?.warnings ?? [];
        const explanation = [
          value?.message,
          value?.source ? `Source: ${value.source}` : "Quantity resolution unavailable",
          ...warnings,
        ].filter(Boolean).join(" · ");
        return (
          <span key={kind} title={explanation}>
            {kind === "total" ? "Total" : "Remaining"}:{" "}
            <span className={resolved ? "font-mono" : "text-accent-orange"}>
              {resolved ? value.count : "Unknown"}
            </span>
            {warnings.length > 0 && <span className="text-accent-orange"> · historical estimate</span>}
            {value?.message && <span className="block text-accent-orange">{value.message}</span>}
          </span>
        );
      })}
    </span>
  );
}
