"use client";

/** Rendszer-kézikönyv: a felület működése és folyamatai fejezetenként,
 *  azonnali kereséssel — ugyanaz a tudás, amiben az AI-asszisztens keres
 *  (how_to eszköz). Később az onboarding alapja. */

import { useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Section { key: string; title: string; keywords: string; body: string }

function renderBody(body: string) {
  // egyszerű markdown-szerű megjelenítés: ## alcím, 1. lépés, - felsorolás, egyéb bekezdés
  const out: React.ReactNode[] = [];
  let list: { kind: "ol" | "ul"; items: string[] } | null = null;
  const flush = () => {
    if (!list) return;
    const Tag = list.kind;
    out.push(
      <Tag key={out.length} className={`${list.kind === "ol" ? "list-decimal" : "list-disc"} space-y-1 pl-5 text-sm text-slate-700`}>
        {list.items.map((x, i) => <li key={i}>{x}</li>)}
      </Tag>,
    );
    list = null;
  };
  for (const raw of body.split("\n")) {
    const line = raw.trim();
    if (!line) { flush(); continue; }
    if (line.startsWith("## ")) { flush(); out.push(<h3 key={out.length} className="mt-4 text-sm font-semibold uppercase tracking-wide text-indigo-700">{line.slice(3)}</h3>); continue; }
    const m = line.match(/^(\d+)\.\s+(.*)$/);
    if (m) { if (!list || list.kind !== "ol") { flush(); list = { kind: "ol", items: [] }; } list.items.push(m[2]); continue; }
    if (line.startsWith("- ")) { if (!list || list.kind !== "ul") { flush(); list = { kind: "ul", items: [] }; } list.items.push(line.slice(2)); continue; }
    flush();
    out.push(<p key={out.length} className="text-sm text-slate-700">{line}</p>);
  }
  flush();
  return out;
}

export default function KezikonyvPage() {
  const { t } = useT();
  const { toast } = useUI();
  const [sections, setSections] = useState<Section[]>([]);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Section[] | null>(null);
  const [active, setActive] = useState<string | null>(null);

  useEffect(() => {
    api.get<{ sections: Section[] }>("/api/assistant/manual").then((r) => { setSections(r.sections); setActive((a) => a ?? r.sections[0]?.key ?? null); }).catch((e) => toast(errorMessage(e), "error"));
  }, [toast]);

  useEffect(() => {
    const needle = q.trim();
    if (needle.length < 2) { setHits(null); return; }
    const id = setTimeout(() => {
      api.get<{ sections: Section[] }>(`/api/assistant/manual?q=${encodeURIComponent(needle)}`).then((r) => { setHits(r.sections); if (r.sections[0]) setActive(r.sections[0].key); }).catch(() => setHits([]));
    }, 250);
    return () => clearTimeout(id);
  }, [q]);

  const list = hits ?? sections;
  const current = useMemo(() => sections.find((s) => s.key === active) ?? null, [sections, active]);

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-xl font-semibold">📖 {t("manual.title")}</h1>
          <p className="text-sm text-slate-500">{t("manual.subtitle")}</p>
        </div>
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={`🔍 ${t("manual.search")}`}
          className="ml-auto w-72 max-w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        />
      </div>
      <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
        <aside className="rounded-2xl border border-slate-200 bg-white p-2 shadow-sm">
          {hits && <p className="px-2 py-1 text-xs text-slate-500">{t("manual.hits", { n: hits.length })}</p>}
          {list.length === 0 && <p className="px-2 py-3 text-sm text-slate-400">{t("manual.empty")}</p>}
          {list.map((s) => (
            <button
              key={s.key}
              onClick={() => setActive(s.key)}
              className={`block w-full rounded-lg px-3 py-2 text-left text-sm ${s.key === active ? "bg-slate-100 font-medium text-indigo-700" : "hover:bg-slate-50"}`}
            >
              {s.title}
            </button>
          ))}
        </aside>
        <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          {current ? (
            <>
              <h2 className="text-lg font-semibold">{current.title}</h2>
              <div className="mt-2 space-y-2">{renderBody(current.body)}</div>
              <p className="mt-6 text-xs text-slate-400">{t("manual.askAi")}</p>
            </>
          ) : (
            <p className="text-sm text-slate-400">{t("manual.pick")}</p>
          )}
        </article>
      </div>
    </AppShell>
  );
}
