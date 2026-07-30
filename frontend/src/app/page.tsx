export default function Home() {
  return (
    <>
      <p className="text-sm text-text-muted">🧪 Option Income Lab</p>
      <h1 className="mt-2 text-4xl font-medium tracking-tight">
        Where boring dividends get interesting
      </h1>
      <p className="mt-4 max-w-2xl text-text-muted">
        New Next.js frontend — dark theme scaffolded from the existing design
        system. This is a placeholder while pages are ported from the Python app.
      </p>

      <div className="mt-10 grid gap-4 sm:grid-cols-3">
        {[
          { label: "Calls Exposure", value: "$—", accent: "text-accent-blue" },
          { label: "Puts Committed", value: "$—", accent: "text-accent-blue" },
          { label: "Avg RoC · annualized", value: "—%", accent: "text-accent-green" },
        ].map((c) => (
          <div
            key={c.label}
            className="rounded-[var(--radius-card)] border border-border bg-bg-card p-5"
          >
            <div className={`font-mono text-3xl ${c.accent}`}>{c.value}</div>
            <div className="mt-1 text-sm text-text-muted">{c.label}</div>
          </div>
        ))}
      </div>

      <div className="mt-10 flex gap-3">
        <button className="rounded-[var(--radius-pill)] bg-text px-8 py-3.5 font-medium text-bg transition hover:opacity-85">
          Primary pill
        </button>
        <button className="rounded-[var(--radius-pill)] border-2 border-text bg-transparent px-8 py-3.5 font-medium text-text transition hover:opacity-85">
          Outlined pill
        </button>
      </div>
    </>
  );
}
