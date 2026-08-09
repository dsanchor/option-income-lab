"use client";

import { useMemo, useState } from "react";
import { Bot, LoaderCircle, RotateCcw, Save } from "lucide-react";
import type {
  AiFunctionSetting,
  AiProvidersConfig,
  AiSettingSource,
} from "@/types/aiProviders";

const inputCls =
  "w-full rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 font-mono text-sm text-text focus:border-accent-blue focus:outline-none";

function sourceLabel(source: AiSettingSource) {
  if (source === "override") return "Override";
  if (source === "legacy") return "Legacy override";
  return "Inherited";
}

export default function AiProvidersView({ initial }: { initial: AiProvidersConfig }) {
  const [functions, setFunctions] = useState(initial.functions);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<{ ok: boolean; message: string } | null>(null);

  const groups = useMemo(() => {
    const result = new Map<string, AiFunctionSetting[]>();
    for (const item of functions) {
      result.set(item.group, [...(result.get(item.group) ?? []), item]);
    }
    return [...result.entries()];
  }, [functions]);

  function update(
    id: string,
    patch: Partial<AiFunctionSetting>,
  ) {
    setFunctions((current) =>
      current.map((item) => item.id === id ? { ...item, ...patch } : item),
    );
    setStatus(null);
  }

  function reset(item: AiFunctionSetting) {
    update(item.id, {
      provider: "",
      model: "",
      effective_provider: item.inherited_provider,
      effective_model: item.inherited_model,
      provider_source: "inherited",
      model_source: "inherited",
    });
  }

  async function save() {
    setSaving(true);
    setStatus(null);
    try {
      const res = await fetch("/api/settings/ai-providers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          functions: Object.fromEntries(
            functions.map((item) => [
              item.id,
              { provider: item.provider, model: item.model },
            ]),
          ),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        throw new Error(data.error || "Save failed");
      }
      setFunctions(data.functions);
      setStatus({ ok: true, message: "AI provider settings saved." });
    } catch (err) {
      setStatus({
        ok: false,
        message: err instanceof Error ? err.message : "Network error",
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      {groups.map(([group, items]) => (
        <section key={group} className="rounded-[var(--radius)] border border-border bg-bg-card p-5">
          <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold">
            <Bot size={18} className="text-accent-blue" aria-hidden />
            {group}
          </h2>
          <div className="grid gap-4 xl:grid-cols-2">
            {items.map((item) => {
              const effectiveProvider = item.provider || item.inherited_provider;
              const effectiveModel = item.model || item.inherited_model;
              return (
                <fieldset
                  key={item.id}
                  className="rounded-[var(--radius)] border border-border bg-bg-input/30 p-4"
                >
                  <legend className="px-1 text-sm font-semibold text-text">{item.label}</legend>
                  <p className="mb-3 text-xs text-text-muted">
                    <code>{item.id}</code> · {item.description}
                  </p>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="grid gap-1 text-xs font-medium text-text-muted">
                      Provider
                      <select
                        className={inputCls}
                        value={item.provider}
                        onChange={(e) => update(item.id, {
                          provider: e.target.value,
                          effective_provider: e.target.value || item.inherited_provider,
                          provider_source: e.target.value ? "override" : "inherited",
                        })}
                      >
                        <option value="">Inherit ({item.inherited_provider})</option>
                        {initial.providers.map((provider) => (
                          <option key={provider} value={provider}>
                            {provider === "azure" ? "Azure" : "Gemini"}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="grid gap-1 text-xs font-medium text-text-muted">
                      Model
                      <input
                        className={inputCls}
                        value={item.model}
                        onChange={(e) => update(item.id, {
                          model: e.target.value,
                          effective_model: e.target.value || item.inherited_model,
                          model_source: e.target.value ? "override" : "inherited",
                        })}
                        placeholder={item.inherited_model}
                        autoComplete="off"
                        spellCheck={false}
                      />
                    </label>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
                    <span className="rounded-[var(--radius-pill)] border border-border bg-bg-card px-2.5 py-1 text-text-muted">
                      Provider: <strong className="text-text">{effectiveProvider}</strong>
                      {" · "}{sourceLabel(item.provider_source)}
                    </span>
                    <span className="rounded-[var(--radius-pill)] border border-border bg-bg-card px-2.5 py-1 text-text-muted">
                      Model: <strong className="text-text">{effectiveModel}</strong>
                      {" · "}{sourceLabel(item.model_source)}
                    </span>
                    <button
                      type="button"
                      onClick={() => reset(item)}
                      className="ml-auto inline-flex items-center gap-1 rounded-[var(--radius-pill)] border border-border px-2.5 py-1 text-text-muted transition-colors hover:bg-bg-hover hover:text-text"
                    >
                      <RotateCcw size={12} aria-hidden />
                      Reset
                    </button>
                  </div>
                </fieldset>
              );
            })}
          </div>
        </section>
      ))}

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border border-accent-green/50 bg-accent-green/15 px-6 py-2 text-sm font-medium text-accent-green transition-colors hover:bg-accent-green/25 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? (
            <LoaderCircle size={15} className="animate-spin" aria-hidden />
          ) : (
            <Save size={15} aria-hidden />
          )}
          {saving ? "Saving…" : "Save"}
        </button>
        {status && (
          <p
            aria-live="polite"
            className={`text-sm ${status.ok ? "text-accent-green" : "text-accent-red"}`}
          >
            {status.message}
          </p>
        )}
      </div>
    </div>
  );
}
