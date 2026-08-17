"use client";

/** Általános multifunkciós kereső-választó (combobox) a legördülők helyett:
 *  szabad szavas, ékezet-érzéketlen, több szavas szűrés a címkére, alcímre és
 *  jelvényre. A PartnerPicker mintája — bármilyen entitáshoz (termék, raktár,
 *  szállító, felhasználó). */

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useT } from "@/lib/i18n";

export interface SearchItem {
  id: string;
  label: string;
  sublabel?: string | null; // pl. kategória · egység
  badge?: string | null; // rövid kód/jelvény a sor végén
  inactive?: boolean;
}

function norm(s: string | null | undefined): string {
  return (s ?? "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "");
}

const MAX_SHOWN = 50;

export default function SearchSelect({
  items,
  value,
  onChange,
  placeholder,
  required = false,
  allowEmpty = false,
  emptyLabel,
  className = "",
}: {
  items: SearchItem[];
  value: string;
  onChange: (id: string) => void;
  placeholder?: string;
  required?: boolean;
  /** Üres választás felkínálása a lista tetején (pl. „— nincs —"). */
  allowEmpty?: boolean;
  emptyLabel?: string;
  className?: string;
}) {
  const { t } = useT();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  // A lista PORTÁLBAN nyílik (document.body), fix pozícióval — így az
  // overflow-os táblázat-konténerek nem vágják le a találatokat.
  const [rect, setRect] = useState<{ top: number; left: number; width: number } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function place() {
      const r = rootRef.current?.getBoundingClientRect();
      if (r) setRect({ top: r.bottom + 4, left: r.left, width: Math.max(r.width, 256) });
    }
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open]);

  const selected = useMemo(() => items.find((i) => i.id === value) ?? null, [items, value]);

  const results = useMemo(() => {
    const tokens = norm(query).split(/\s+/).filter(Boolean);
    if (!tokens.length) return items;
    return items.filter((it) => {
      const h = norm([it.label, it.sublabel, it.badge].filter(Boolean).join(" "));
      return tokens.every((tok) => h.includes(tok));
    });
  }, [items, query]);

  useEffect(() => {
    function onDown(e: MouseEvent) {
      const target = e.target as Node;
      if (
        rootRef.current && !rootRef.current.contains(target)
        && !(listRef.current && listRef.current.contains(target))
      ) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  useEffect(() => setHighlight(0), [query]);

  useEffect(() => {
    listRef.current
      ?.querySelector(`[data-idx="${highlight}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [highlight]);

  function pick(id: string) {
    onChange(id);
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
      const it = results[highlight];
      if (it) pick(it.id);
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
          placeholder={selected ? undefined : placeholder ?? t("picker.placeholder")}
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
            <span className="truncate font-medium">{selected.label}</span>
            {selected.sublabel && (
              <span className="shrink-0 truncate text-xs text-slate-400">{selected.sublabel}</span>
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

      {open && rect && createPortal(
        <div
          ref={listRef}
          style={{ position: "fixed", top: rect.top, left: rect.left, width: rect.width }}
          className="z-[70] max-h-80 overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-lg"
        >
          {allowEmpty && (
            <button
              type="button"
              onClick={() => pick("")}
              className="block w-full border-b border-slate-100 px-3 py-2 text-left text-sm text-slate-500"
            >
              {emptyLabel ?? "—"}
            </button>
          )}
          {shown.length === 0 && (
            <div className="px-3 py-3 text-sm text-slate-500">{t("picker.noResults")}</div>
          )}
          {shown.map((it, i) => (
            <button
              key={it.id}
              type="button"
              data-idx={i}
              onMouseEnter={() => setHighlight(i)}
              onClick={() => pick(it.id)}
              className={`block w-full px-3 py-2 text-left text-sm ${
                i === highlight ? "bg-blue-50" : ""
              } ${it.id === value ? "font-semibold" : ""}`}
            >
              <div className="flex items-center gap-2">
                <span className="truncate">{it.label}</span>
                {it.badge && (
                  <span className="shrink-0 font-mono text-xs text-slate-400">{it.badge}</span>
                )}
                {it.inactive && (
                  <span className="shrink-0 rounded-full bg-slate-100 px-2 text-[10px] text-slate-500">
                    {t("picker.inactive")}
                  </span>
                )}
              </div>
              {it.sublabel && (
                <div className="truncate text-xs text-slate-400">{it.sublabel}</div>
              )}
            </button>
          ))}
          {results.length > MAX_SHOWN && (
            <div className="border-t border-slate-100 px-3 py-2 text-xs text-slate-400">
              {t("picker.more", { count: results.length - MAX_SHOWN })}
            </div>
          )}
        </div>,
        document.body,
      )}
    </div>
  );
}
