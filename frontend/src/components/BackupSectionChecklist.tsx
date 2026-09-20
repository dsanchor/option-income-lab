import type { BackupSectionDefinition } from "@/types/backup";

export default function BackupSectionChecklist({
  sections,
  selected,
  onChange,
  disabled,
}: {
  sections: Array<BackupSectionDefinition | string>;
  selected: string[];
  onChange: (sections: string[]) => void;
  disabled?: boolean;
}) {
  return (
    <fieldset disabled={disabled}>
      <legend className="mb-3 text-sm font-semibold">Sections</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {sections.map((section) => {
          const definition = typeof section === "string" ? { name: section } : section;
          const id = definition.id ?? definition.name;
          const checked = selected.includes(id);
          return (
            <label
              key={id}
              className="flex cursor-pointer gap-3 rounded-[var(--radius)] border border-border bg-bg-input p-3"
            >
              <input
                type="checkbox"
                checked={checked}
                disabled={disabled || definition.required}
                onChange={(event) =>
                  onChange(
                    event.target.checked
                      ? [...selected, id]
                      : selected.filter((value) => value !== id),
                  )
                }
                className="mt-1 accent-[var(--color-accent-blue)]"
              />
              <span>
                <span className="block text-sm font-medium">
                  {definition.label ?? definition.name.replaceAll("_", " ")}
                </span>
                {definition.description ? (
                  <span className="block text-xs text-text-muted">{definition.description}</span>
                ) : null}
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
