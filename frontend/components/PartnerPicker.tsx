"use client";

/** Multifunkciós partner-kereső (combobox): névre, PT-kódra, adószámra,
 * kapcsolattartóra, telefonszámra és városra is szűr, ékezet-érzéketlenül.
 * A legördülő nagy partnerlistáknál használhatatlan — ez váltja ki. */

import { useEffect, useMemo, useRef, useState } from "react";
import { useT } from "@/lib/i18n";

export interface PickerPartner {
  id: string;
  name: string;
  company_name?: string | null;
  partner_code?: string | null;
  tax_number?: string | null;
  contact_name?: string | null;
  contact_phone?: string | null;
  address_city?: string | null;
  is_active?: boolean;
}

/** Ékezetek eltávolítása + kisbetűsítés a toleráns kereséshez. */
function norm(s: string | null | undefined): string {
  return (s ?? "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");
}

function haystack(p: PickerPartner): string {
  return norm(
    [p.name, p.company_name, p.partner_code, p.tax_number, p.contact_name, p.contact_phone, p.address_city]
      .filter(Boolean)
      .join(" ")
  );
}

const MAX_SHOWN = 50;

export default function PartnerPicker({
  partners,
  value,
  onChange,
  placeholder,
  includeInactive = false,
  required = false,
  className = "",
}: {
  partners: PickerPartner[];
  value: string;
  onChange: (id: string) => void;
  placeholder?: string;
  includeInactive?: boolean;
  required?: boolean;
  className?: string;
}) {
  const { t } = useT();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const pool = useMemo(
    () => (includeInactive ? partners : partners.filter((p) => p.is_active !== false)),
    [partners, includeInactive]
  );
  const selected = useMemo(() => partners.find((p) => p.id === value) ?? null, [partners, value]);

  const results = useMemo(() => {
    const tokens = norm(query).split(/\s+/).filter(Boolean);
    if (!tokens.length) return pool;
    return pool.filter((p) => {
      const h = haystack(p);
      return tokens.every((tok) => h.includes(tok));
    });
  }, [pool, query]);

  // kattintás a komponensen kívülre → zárás
  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  useEffect(() => setHighlight(0), [query]);

  // a kiemelt elem maradjon látható görgetésnél
  useEffect(() => {
    listRef.current
      ?.querySelector(`[data-idx="${highlight}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [highlight]);

  function pick(p: PickerPartner) {
    onChange(p.id);
    setQuery("");
    setOpen(false);
  }

  function clear() {
    onChange("");
    setQuery("");
    setOpen(false);
    inputRef.current?.focus();
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (!open && (e.key === "ArrowDown" || e.key === "Enter")) {
      setOpen(true);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, Math.min(results.length, MAX_SHOWN) - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const p = results[highlight];
      if (p) pick(p);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  const shown = results.slice(0, MAX_SHOWN);

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <div className="flex items-center rounded-lg border border-slate-300 bg-white pr-1 focus-within:border-blue-400">
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-expanded={open}
          required={required && !value}
          value={open ? query : selected ? "" : query}
          placeholder={
            selected
              ? undefined
              : placeholder ?? t("picker.placeholder")
          }
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onKeyDown={onKeyDown}
          className="w-full min-w-0 bg-transparent px-3 py-2 text-sm outline-none"
        />
        {selected && !open && (
          <button
            type="button"
            onClick={() => {
              setOpen(true);
              setQuery("");
              inputRef.current?.focus();
            }}
            className="absolute inset-y-0 left-0 right-8 flex items-center gap-2 px-3 text-left text-sm"
          >
            <span className="truncate font-medium">{selected.name}</span>
            {selected.partner_code && (
              <span className="shrink-0 font-mono text-xs text-slate-400">{selected.partner_code}</span>
            )}
          </button>
        )}
        {(selected || query) && (
          <button
            type="button"
            onClick={clear}
            title={t("picker.clear")}
            className="rounded px-1.5 py-0.5 text-sm text-slate-400 hover:text-slate-700"
          >
            ✕
          </button>
        )}
      </div>

      {open && (
        <div
          ref={listRef}
          className="absolute z-30 mt-1 max-h-80 w-full min-w-72 overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-lg"
        >
          {shown.length === 0 && (
            <div className="px-3 py-3 text-sm text-slate-500">{t("picker.noResults")}</div>
          )}
          {shown.map((p, i) => (
            <button
              key={p.id}
              type="button"
              data-idx={i}
              onMouseEnter={() => setHighlight(i)}
              onClick={() => pick(p)}
              className={`block w-full px-3 py-2 text-left text-sm ${
                i === highlight ? "bg-blue-50" : ""
              } ${p.id === value ? "font-semibold" : ""}`}
            >
              <div className="flex items-center gap-2">
                <span className="truncate">{p.name}</span>
                {p.partner_code && (
                  <span className="shrink-0 font-mono text-xs text-slate-400">{p.partner_code}</span>
                )}
                {p.is_active === false && (
                  <span className="shrink-0 rounded-full bg-slate-100 px-2 text-[10px] text-slate-500">
                    {t("picker.inactive")}
                  </span>
                )}
              </div>
              {(p.address_city || p.contact_name || p.tax_number) && (
                <div className="truncate text-xs text-slate-400">
                  {[p.address_city, p.contact_name, p.tax_number].filter(Boolean).join(" · ")}
                </div>
              )}
            </button>
          ))}
          {results.length > MAX_SHOWN && (
            <div className="border-t border-slate-100 px-3 py-2 text-xs text-slate-400">
              {t("picker.more", { count: results.length - MAX_SHOWN })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
