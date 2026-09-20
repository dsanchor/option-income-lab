import type { BackupPreset, BackupPresetDefinition } from "@/types/backup";

const FALLBACK_PRESETS: BackupPresetDefinition[] = [
  {
    id: "recommended_full_user_backup",
    label: "Recommended full backup",
    description: "All user-created portfolio, symbol, option, plan, and functional settings data.",
    recommended: true,
  },
  {
    id: "portfolio_only",
    label: "Portfolio only",
    description: "Accounts, complete ledger history, and required references.",
  },
  {
    id: "symbols_and_configuration",
    label: "Symbols and configuration",
    description: "Security identities, watchlist configuration, option positions, and plans.",
  },
  {
    id: "custom",
    label: "Custom",
    description: "Choose sections and filters. Dependencies may expand the final scope.",
  },
];

export default function BackupPresetSelector({
  presets,
  value,
  onChange,
  disabled,
}: {
  presets: BackupPresetDefinition[];
  value: BackupPreset;
  onChange: (preset: BackupPreset) => void;
  disabled?: boolean;
}) {
  const items = presets.length ? presets : FALLBACK_PRESETS;
  return (
    <fieldset disabled={disabled}>
      <legend className="mb-3 text-sm font-semibold">Backup preset</legend>
      <div className="grid gap-3 md:grid-cols-2">
        {items.map((preset) => {
          const label = preset.label ?? preset.name ?? preset.id.replaceAll("_", " ");
          return (
            <label
              key={preset.id}
              className={`cursor-pointer rounded-[var(--radius)] border p-4 transition-colors ${
                value === preset.id
                  ? "border-accent-blue bg-accent-blue/10"
                  : "border-border bg-bg-card-2 hover:border-accent-blue/50"
              }`}
            >
              <span className="flex items-start gap-3">
                <input
                  type="radio"
                  name="backup-preset"
                  value={preset.id}
                  checked={value === preset.id}
                  onChange={() => onChange(preset.id)}
                  className="mt-1 accent-[var(--color-accent-blue)]"
                />
                <span>
                  <span className="block text-sm font-semibold">
                    {label}
                    {preset.recommended || preset.id === "recommended_full_user_backup" ? (
                      <span className="ml-2 rounded-full bg-accent-green/15 px-2 py-0.5 text-[0.68rem] text-accent-green">
                        Recommended
                      </span>
                    ) : null}
                  </span>
                  {preset.description ? (
                    <span className="mt-1 block text-xs leading-relaxed text-text-muted">
                      {preset.description}
                    </span>
                  ) : null}
                </span>
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
