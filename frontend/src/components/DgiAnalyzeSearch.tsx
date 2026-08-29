"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

/** Inline "analyze another symbol" search that navigates to /screener/dgi/analyze/{symbol}. */
export default function DgiAnalyzeSearch() {
  const router = useRouter();
  const [value, setValue] = useState("");

  function go() {
    const s = value.trim().toUpperCase();
    if (s) router.push(`/screener/dgi/analyze/${encodeURIComponent(s)}`);
  }

  return (
    <div className="flex items-center gap-2">
      <input
        type="text"
        maxLength={10}
        value={value}
        onChange={(e) => setValue(e.target.value.toUpperCase())}
        onKeyDown={(e) => e.key === "Enter" && go()}
        placeholder="Analyze another symbol…"
        className="w-[180px] rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 font-mono text-sm text-text focus:border-accent-blue focus:outline-none"
      />
      <button
        type="button"
        onClick={go}
        className="rounded-[var(--radius-pill)] bg-accent-blue px-4 py-2 text-sm text-white"
      >
        Go
      </button>
    </div>
  );
}
