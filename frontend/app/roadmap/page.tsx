"use client";

/** Termék-roadmap (115. kör): a tartalmat az üzemeltető (Flotta-pult) tölti,
 *  itt csak olvasható — három csoport: fejlesztés alatt, tervben, elkészült. */

import { useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

type Item = {
  id: string;
  title: string;
  description: string;
  status: "planned" | "in_progress" | "done";
  eta: string;
};

const SECTIONS: { status: Item["status"]; icon: string; cls: string; dot: string }[] = [
  { status: "in_progress", icon: "🔨", cls: "border-sky-200 bg-sky-50", dot: "bg-sky-500" },
  { status: "planned", icon: "🗓", cls: "border-amber-200 bg-amber-50", dot: "bg-amber-500" },
  { status: "done", icon: "✅", cls: "border-emerald-200 bg-emerald-50", dot: "bg-emerald-500" },
];

export default function RoadmapPage() {
  const { t } = useT();
  const [items, setItems] = useState<Item[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    api.get<Item[]>("/api/roadmap")
      .then(setItems)
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

  return (
    <AppShell>
      <h1 className="mb-1 text-xl font-bold">🗺 {t("road.title")}</h1>
      <p className="mb-5 text-sm text-slate-500">{t("road.hint")}</p>
      {loaded && items.length === 0 && (
        <p className="rounded-2xl border border-slate-200 bg-white px-4 py-10 text-center text-slate-400">{t("road.empty")}</p>
      )}
      <div className="grid gap-6 lg:grid-cols-3">
        {SECTIONS.map(({ status, icon, cls, dot }) => {
          const group = items.filter((i) => i.status === status);
          if (loaded && items.length > 0 && group.length === 0) {
            return (
              <section key={status}>
                <h2 className="mb-2 flex items-center gap-2 font-semibold">{icon} {t(`road.${status}`)} <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-500">0</span></h2>
                <p className="text-sm text-slate-400">{t("road.none")}</p>
              </section>
            );
          }
          if (group.length === 0) return null;
          return (
            <section key={status}>
              <h2 className="mb-2 flex items-center gap-2 font-semibold">
                {icon} {t(`road.${status}`)}
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-500">{group.length}</span>
              </h2>
              <ol className="relative space-y-3 border-l border-slate-200 pl-5">
                {group.map((i) => (
                  <li key={i.id} className={`relative rounded-xl border px-4 py-3 ${cls}`}>
                    <span className={`absolute -left-[26px] top-4 h-3 w-3 rounded-full ${dot}`} aria-hidden="true" />
                    <div className="flex flex-wrap items-center gap-2">
                      <b>{i.title}</b>
                      {i.eta && (
                        <span className="rounded-full border border-current/30 px-2 py-0.5 text-xs font-medium opacity-80">{i.eta}</span>
                      )}
                    </div>
                    {i.description && (
                      <p className="mt-1 whitespace-pre-line text-sm text-slate-600">{i.description}</p>
                    )}
                  </li>
                ))}
              </ol>
            </section>
          );
        })}
      </div>
    </AppShell>
  );
}
