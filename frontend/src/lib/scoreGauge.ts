export function scoreGaugeStyle(score: number, colored = true) {
  return {
    // Keep the color scale at the track width; only its visible portion changes.
    width: "100%",
    clipPath: `inset(0 ${100 - Math.min(100, Math.max(0, score))}% 0 0)`,
    background: colored
      ? "linear-gradient(90deg, var(--accent-red) 0%, var(--accent-red) 22%, var(--accent-orange) 28%, var(--accent-orange) 47%, var(--color-yellow-400) 53%, var(--color-yellow-400) 72%, var(--accent-green) 78%, var(--accent-green) 100%)"
      : undefined,
  };
}
