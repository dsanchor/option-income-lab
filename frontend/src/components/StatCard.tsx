import type { ReactNode } from "react";
import AnimatedNumber from "@/components/AnimatedNumber";

type Tone = "blue" | "green" | "red" | "orange" | "purple" | "neutral";

const TONE: Record<Tone, { text: string; bar: string; glow: string }> = {
  blue:    { text: "text-accent-blue",   bar: "var(--grad-blue)",   glow: "rgba(91,97,255,0.14)" },
  green:   { text: "text-accent-green",  bar: "var(--grad-green)",  glow: "rgba(0,196,147,0.14)" },
  red:     { text: "text-accent-red",    bar: "var(--grad-warm)",   glow: "rgba(255,77,94,0.14)" },
  orange:  { text: "text-accent-orange", bar: "var(--grad-warm)",   glow: "rgba(255,148,22,0.14)" },
  purple:  { text: "text-accent-purple", bar: "var(--grad-purple)", glow: "rgba(167,139,250,0.14)" },
  neutral: { text: "text-text",          bar: "linear-gradient(135deg,#3a3f47,#4b515a)", glow: "rgba(255,255,255,0.05)" },
};

/**
 * Rich KPI card: gradient accent bar, optional icon, animated number.
 * Pass a numeric `value` to animate; pass `display` for non-numeric values.
 */
export default function StatCard({
  label,
  value,
  display,
  prefix = "",
  suffix = "",
  decimals = 0,
  tone = "neutral",
  icon,
  hint,
}: {
  label: string;
  value?: number;
  display?: string;
  prefix?: string;
  suffix?: string;
  decimals?: number;
  tone?: Tone;
  icon?: ReactNode;
  hint?: string;
}) {
  const t = TONE[tone];
  return (
    <div
      className="surface card-hover relative flex h-full flex-col overflow-hidden p-5"
      style={{ background: `radial-gradient(120% 120% at 100% 0%, ${t.glow}, transparent 55%), linear-gradient(180deg, var(--bg-card), var(--bg-card-2))` }}
    >
      <span className="absolute inset-y-0 left-0 w-1" style={{ background: t.bar }} aria-hidden />
      <div className="flex items-start justify-between gap-3">
        <div className="text-xs uppercase tracking-wide text-text-muted">{label}</div>
        {icon && (
          <span
            className="grid h-8 w-8 shrink-0 place-items-center rounded-[10px] text-sm"
            style={{ background: t.bar, color: "#fff" }}
          >
            {icon}
          </span>
        )}
      </div>
      <div className={`mt-2 font-mono text-3xl font-semibold tracking-tight ${t.text}`}>
        {value != null ? (
          <AnimatedNumber value={value} prefix={prefix} suffix={suffix} decimals={decimals} />
        ) : (
          display ?? "—"
        )}
      </div>
      {hint && <div className="mt-1 text-xs text-text-muted">{hint}</div>}
    </div>
  );
}
