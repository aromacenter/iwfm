"use client";

/** X-Learning – saját karton: a belépett dolgozó kiosztott tananyagai
 *  csempés galériában. Minden csempe halvány, egymástól eltérő alapszínt és
 *  egy témára utaló, ház-stílusú rajzolt illusztrációt kap (a backend adja:
 *  kézi/AI-generált borító, vagy a címből választott könyvtári rajz). */

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, errorMessage, api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { usePerms } from "@/lib/perms";
import { STATUS_CLS, type MyItem } from "./shared";

export function svgDataUrl(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

export default function LearningHomePage() {
  const { t, lang } = useT();
  const [data, setData] = useState<{ employee_name: string; items: MyItem[] } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [noCard, setNoCard] = useState(false);
  const [reload, setReload] = useState(0);
  const perms = usePerms();
  const fd = (d: string | null) => (d ? new Date(d).toLocaleDateString(lang === "hu" ? "hu-HU" : "en-GB") : "—");

  useEffect(() => {
    api
      .get<{ employee_name: string; items: MyItem[] }>("/api/learning/my")
      .then(setData)
      .catch((err) => {
        if (err instanceof ApiError && err.code === "employee.none_for_user") {
          setProblem(t("learning.my.noEmployee"));
          setNoCard(true);
        }
        else if (err instanceof ApiError && err.status === 403) setProblem(t("learning.my.moduleOff"));
        else if (err instanceof ApiError && err.status === 401) setProblem(null);
        else setProblem(errorMessage(err));
      });
  }, [t, reload]);

  // vezetői fiók karton nélkül: egy gombbal létrehozható a saját karton (142. kör)
  async function createOwnCard() {
    try {
      await api.post("/api/employees/me");
      setProblem(null);
      setNoCard(false);
      setReload((n) => n + 1);
    } catch (err) {
      setProblem(errorMessage(err));
    }
  }

  if (problem) {
    return (
      <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        {problem}
        {noCard && perms.can("employees") && (
          <div className="mt-3">
            <button
              type="button"
              onClick={createOwnCard}
              className="rounded-lg bg-[#E31E24] px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
            >
              {t("learning.my.createCard")}
            </button>
            <p className="mt-1 text-xs text-amber-800">{t("learning.my.createCardHint")}</p>
          </div>
        )}
      </div>
    );
  }
  if (!data) return <p className="text-sm text-slate-500">…</p>;

  const open = data.items.filter((i) => i.status === "open" || i.status === "overdue" || i.status === "expired");
  const done = data.items.filter((i) => i.status === "done");

  function tile(i: MyItem) {
    const meta: string[] = [];
    if (i.category) meta.push(i.category);
    if (i.due_date && i.status !== "done") meta.push(`${t("learning.my.due")}: ${fd(i.due_date)}`);
    if (i.valid_until && i.status === "done") meta.push(`${t("learning.my.validUntil")}: ${fd(i.valid_until)}`);
    return (
      <Link
        key={i.id}
        href={`/learning/${i.id}`}
        className="group flex flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm transition hover:-translate-y-0.5 hover:border-slate-400 hover:shadow-md"
      >
        <div className="relative aspect-[4/3] w-full overflow-hidden" style={{ backgroundColor: i.cover_color || "#e2e8f0" }}>
          {i.cover_svg ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={svgDataUrl(i.cover_svg)}
              alt=""
              className="h-full w-full object-contain p-4 transition group-hover:scale-[1.04]"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-5xl text-slate-400/70">📘</div>
          )}
          <span className={`absolute right-2 top-2 rounded-full px-2 py-0.5 text-[11px] font-semibold shadow ${STATUS_CLS[i.status]}`}>
            {t(`learning.status.${i.status}`)}
          </span>
          <div className="absolute bottom-2 left-2 flex gap-1 text-[11px]">
            {i.has_video && <span className="rounded-full bg-white/80 px-1.5 py-0.5 text-slate-700 shadow-sm">🎬</span>}
            {i.has_test && <span className="rounded-full bg-white/80 px-1.5 py-0.5 text-slate-700 shadow-sm">✅ {t("learning.my.hasTest")}</span>}
          </div>
        </div>
        <div className="flex flex-1 flex-col gap-1 p-3">
          <div className="line-clamp-2 text-sm font-semibold leading-snug">{i.title}</div>
          {meta.length > 0 && <div className="text-xs text-slate-500">{meta.join(" · ")}</div>}
          {i.attempts > 0 && (
            <div className="mt-auto text-xs text-slate-500">
              {t("learning.my.attempts", { n: i.attempts })}
              {i.last_score != null ? ` · ${t("learning.my.bestScore", { n: i.last_score })}` : ""}
            </div>
          )}
        </div>
      </Link>
    );
  }

  const grid = "grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">{t("learning.my.title")}</h1>
        <p className="text-sm text-slate-500">{data.employee_name}</p>
      </div>
      {data.items.length === 0 && (
        <div className="rounded-2xl border border-slate-200 bg-white p-6 text-center text-sm text-slate-500">{t("learning.my.empty")}</div>
      )}
      {open.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{t("learning.my.todo")}</h2>
          <div className={grid}>{open.map(tile)}</div>
        </section>
      )}
      {done.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{t("learning.my.doneSection")}</h2>
          <div className={grid}>{done.map(tile)}</div>
        </section>
      )}
    </div>
  );
}
