export default function SymbolDetailLoading() {
  return (
    <div className="space-y-6" role="status" aria-label="Loading symbol detail">
      <div className="surface h-36 animate-pulse bg-bg-card" />
      <div className="surface p-4">
        <h2 className="mb-3 text-sm font-semibold">Summary</h2>
        <div className="h-16 animate-pulse rounded bg-bg-input" />
      </div>
      <p className="text-sm text-text-muted">Loading symbol…</p>
    </div>
  );
}
