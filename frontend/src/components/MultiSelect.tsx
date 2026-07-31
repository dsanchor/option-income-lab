"use client";

import { useEffect, useRef, useState } from "react";

export interface MultiSelectOption {
  value: string;
  label: string;
}

/**
 * A checkbox dropdown with a "Select all" row, mirroring the legacy
 * `.dropdown-multi` control (months / symbols filters on Economics).
 * Selecting none == "all" (empty array). Closes on outside click.
 */
export default function MultiSelect({
  options,
  selected,
  onChange,
  allLabel,
}: {
  options: MultiSelectOption[];
  selected: string[];
  onChange: (next: string[]) => void;
  allLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  }, [open]);

  const allChecked = options.length > 0 && selected.length === options.length;

  function toggle(value: string) {
    if (selected.includes(value)) onChange(selected.filter((v) => v !== value));
    else onChange([...selected, value]);
  }

  function toggleAll() {
    if (allChecked) onChange([]);
    else onChange(options.map((o) => o.value));
  }

  let btnLabel = `${allLabel} ▾`;
  if (selected.length > 0 && selected.length <= 3) {
    btnLabel =
      options
        .filter((o) => selected.includes(o.value))
        .map((o) => o.label)
        .join(", ") + " ▾";
  } else if (selected.length > 3) {
    btnLabel = `${selected.length} selected ▾`;
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
        className="min-w-[130px] rounded-[var(--radius)] border border-border bg-bg-input px-3 py-1.5 text-left text-sm text-text"
      >
        {btnLabel}
      </button>
      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 max-h-72 min-w-[180px] overflow-auto rounded-[var(--radius)] border border-border bg-bg-card py-1 shadow-lg">
          <label className="flex cursor-pointer items-center gap-2 border-b border-border px-3 py-1.5 text-sm hover:bg-bg-hover">
            <input type="checkbox" checked={allChecked} onChange={toggleAll} />
            <strong>Select all</strong>
          </label>
          {options.map((o) => (
            <label
              key={o.value}
              className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm hover:bg-bg-hover"
            >
              <input
                type="checkbox"
                checked={selected.includes(o.value)}
                onChange={() => toggle(o.value)}
              />
              {o.label}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
