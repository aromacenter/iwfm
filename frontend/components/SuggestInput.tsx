"use client";

/** Szabad szavas mező javaslat-listával (75cd2925): a natív datalist mobilon
 * (Android Chrome) megbízhatatlanul súg, ezért saját lenyíló. A gépelt érték
 * bármi lehet — a lista csak kényelmi találatokat ad a korábbi értékekből. */

import { useMemo, useState } from "react";

export default function SuggestInput({
  value,
  onChange,
  options,
  placeholder,
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  placeholder?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const matches = useMemo(() => {
    const q = value.trim().toLowerCase();
    const base = q ? options.filter((o) => o.toLowerCase().includes(q)) : options;
    // Ha már pont egy javaslat van beírva, nincs mit súgni
    return base.filter((o) => o.toLowerCase() !== q).slice(0, 8);
  }, [value, options]);
  return (
    <div className="relative">
      <input
        value={value}
        onChange={(e) => { onChange(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        placeholder={placeholder}
        className={className ?? "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"}
      />
      {open && matches.length > 0 && (
        <div className="absolute z-20 mt-1 max-h-48 w-full overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-lg">
          {matches.map((m) => (
            <button
              key={m}
              type="button"
              // onMouseDown: a blur előtt fusson le, különben eltűnne a lista
              onMouseDown={(e) => { e.preventDefault(); onChange(m); setOpen(false); }}
              className="block w-full px-3 py-2 text-left text-sm hover:bg-indigo-50"
            >
              {m}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
