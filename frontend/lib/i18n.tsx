"use client";

/** Könnyű i18n réteg: nyelvi fájlok (i18n/hu.json, i18n/en.json) +
 * LanguageProvider + useT() hook + modul-szintű translate() a React-en
 * kívüli helyekhez (pl. api.ts hibaüzenetek).
 *
 * Kulcsok pont-jelöléssel ("nav.dashboard"), {var} interpolációval. */

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import hu from "@/i18n/hu.json";
import en from "@/i18n/en.json";

export type Lang = "hu" | "en";

const DICTS: Record<Lang, Record<string, unknown>> = { hu, en };
const STORAGE_KEY = "iwfm-lang";

let currentLang: Lang = "hu";

function lookup(dict: Record<string, unknown>, key: string): string | undefined {
  let node: unknown = dict;
  for (const part of key.split(".")) {
    if (typeof node !== "object" || node === null) {
      node = undefined;
      break;
    }
    node = (node as Record<string, unknown>)[part];
  }
  if (typeof node === "string") return node;
  // Lapos, pontot tartalmazó levél-kulcsok egy szekción belül —
  // pl. errors["warehouse.has_stock"] az "errors.warehouse.has_stock"-hoz.
  const dot = key.indexOf(".");
  if (dot > 0) {
    const section = dict[key.slice(0, dot)];
    if (typeof section === "object" && section !== null) {
      const flat = (section as Record<string, unknown>)[key.slice(dot + 1)];
      if (typeof flat === "string") return flat;
    }
  }
  return undefined;
}

/** Fordítás a jelenlegi nyelven; hiányzó kulcsnál magyar fallback, majd a kulcs. */
export function translate(key: string, vars?: Record<string, string | number>): string {
  let text = lookup(DICTS[currentLang], key) ?? lookup(DICTS.hu, key) ?? key;
  if (vars) {
    for (const [name, value] of Object.entries(vars)) {
      text = text.replaceAll(`{${name}}`, String(value));
    }
  }
  return text;
}

interface I18nContextValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
}

const I18nContext = createContext<I18nContextValue>({
  lang: "hu",
  setLang: () => {},
  t: translate,
});

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Lang>("hu");

  useEffect(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "hu" || saved === "en") {
      currentLang = saved;
      // Egyszeri, szándékos állapot-hidratálás a mentett nyelvi preferenciából.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLangState(saved);
    }
  }, []);

  const setLang = useCallback((next: Lang) => {
    currentLang = next;
    localStorage.setItem(STORAGE_KEY, next);
    setLangState(next);
    document.documentElement.lang = next;
  }, []);

  const t = useCallback(
    (key: string, vars?: Record<string, string | number>) => translate(key, vars),
    // a lang változására újrarenderel, a translate a currentLang-ot olvassa
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [lang]
  );

  return (
    <I18nContext.Provider value={{ lang, setLang, t }}>{children}</I18nContext.Provider>
  );
}

export function useT() {
  return useContext(I18nContext);
}

/** Kis HU/EN kapcsoló — fejlécbe és publikus oldalakra. */
export function LanguageSwitcher({ dark = false }: { dark?: boolean }) {
  const { lang, setLang } = useT();
  return (
    <div
      className={`flex overflow-hidden rounded-lg border text-xs font-semibold ${
        dark ? "border-slate-600" : "border-slate-300"
      }`}
    >
      {(["hu", "en"] as const).map((value) => (
        <button
          key={value}
          onClick={() => setLang(value)}
          className={`px-2 py-1 uppercase ${
            lang === value
              ? "bg-indigo-600 text-white"
              : dark
                ? "bg-slate-800 text-slate-400 hover:text-white"
                : "bg-white text-slate-500 hover:bg-slate-100"
          }`}
        >
          {value}
        </button>
      ))}
    </div>
  );
}
