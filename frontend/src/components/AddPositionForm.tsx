"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

/** Add a manual position (call/put) for this symbol. Mirrors the legacy "Add Position" form. */
export default function AddPositionForm({ symbol }: { symbol: string }) {
  const router = useRouter();
  const [type, setType] = useState<"call" | "put">("call");
  const [strike, setStrike] = useState("");
  const [expiration, setExpiration] = useState("");
  const [premium, setPremium] = useState("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  async function submit() {
    setError(null);
    setSuccess(null);
    if (!strike || !expiration) {
      setError("Strike and expiration are required.");
      return;
    }
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        type,
        strike: parseFloat(strike),
        expiration,
        notes: notes.trim(),
      };
      if (premium) payload.premium = parseFloat(premium);
      const res = await fetch(`/api/symbols/${encodeURIComponent(symbol)}/positions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || "Failed to add position.");
      } else {
        setSuccess("Position added!");
        setStrike("");
        setExpiration("");
        setPremium("");
        setNotes("");
        setTimeout(() => router.refresh(), 700);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="rounded-[var(--radius)] border border-border bg-bg-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-text">Add Position</h3>
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={type}
          onChange={(e) => setType(e.target.value as "call" | "put")}
          className="rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm text-text"
        >
          <option value="call">Call</option>
          <option value="put">Put</option>
        </select>
        <input
          type="number"
          step="0.5"
          placeholder="Strike"
          value={strike}
          onChange={(e) => setStrike(e.target.value)}
          className="w-28 rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm text-text"
        />
        <input
          type="date"
          value={expiration}
          onChange={(e) => setExpiration(e.target.value)}
          className="rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm text-text"
        />
        <input
          type="number"
          step="0.01"
          placeholder="Premium (optional)"
          value={premium}
          onChange={(e) => setPremium(e.target.value)}
          className="w-36 rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm text-text"
        />
        <input
          type="text"
          placeholder="Notes (optional)"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          className="min-w-[10rem] flex-1 rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm text-text"
        />
        <button
          type="button"
          onClick={submit}
          disabled={saving}
          className="rounded-[var(--radius-pill)] bg-accent-blue px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
        >
          {saving ? "Adding…" : "+ Add"}
        </button>
      </div>
      {error && <p className="mt-2 text-sm text-accent-red">{error}</p>}
      {success && <p className="mt-2 text-sm text-accent-green">{success}</p>}
    </section>
  );
}
