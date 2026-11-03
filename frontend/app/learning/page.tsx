"use client";

/** X-Learning – saját karton: a belépett dolgozó kiosztott tananyagai,
 *  állapot, határidő, érvényesség. Mást nem lát (a backend is így adja). */

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, errorMessage, api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { STATUS_CLS, type MyItem } from "./shared";

export default function LearningHomePage() {
  const { t, lang } = useT();
  const [data, setData] = useState<{ employee_name: string; items: MyItem[] } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const fd = (d: string | null) => (d ? new Date(d).toLocaleDateString(lang === "hu" ? "hu-HU" : "en-GB") : "—");

  useEffect(() => {
    api
      .get<{ employee_name: string; items: MyItem[] }>("/api/learning/my")
      .then(setData)
      .catch((err) => {
        if (err instanceof ApiError && err.code === "employee.none_for_user") setProblem(t("learning.my.noEmployee"));
        else if (err instanceof ApiError && err.status === 403) setProblem(t("learning.my.moduleOff"));
        else if (err instanceof ApiError && err.status === 401) setProblem(null);
        else setProblem(errorMessage(err));
      });
  }, [t]);

  if (problem) {
    return <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">{problem}</div>;
  }
  if (!data) return <p className="text-sm text-slate-500">…</p>;

  const open = data.items.filter((i) => i.status === "open" || i.status === "overdue" || i.status === "expired");
  const done = data.items.filter((i) => i.status === "done");

  function card(i: MyItem) {
    return (
      <Link
        key={i.id}
        href={`/learning/${i.id}`}
        className="block rounded-2xl border border-slate-200 bg-white p-4 shadow-sm transition hover:border-slate-400"
      >
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <div className="font-semibold">{i.title}</div>
            {i.category && <div className="text-xs text-slate-500">{i.category}</div>}
          </div>
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_CLS[i.status]}`}>{t(`learning.status.${i.status}`)}</span>
        </div>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
          {i.due_date && (
            <span>
              {t("learning.my.due")}: <b>{fd(i.due_date)}</b>
            </span>
          )}
          {i.valid_until && (
            <span>
              {t("learning.my.validUntil")}: <b>{fd(i.valid_until)}</b>
            </span>
          )}
          {i.has_video && <span>🎬 {t("learning.my.hasVideo")}</span>}
          <span>{i.has_test ? `✅ ${t("learning.my.hasTest")}` : t("learning.my.noTest")}</span>
          {i.attempts > 0 && (
            <span>
              {t("learning.my.attempts", { n: i.attempts })}
              {i.last_score != null ? ` · ${t("learning.my.bestScore", { n: i.last_score })}` : ""}
            </span>
          )}
        </div>
      </Link>
    );
  }

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
          {open.map(card)}
        </section>
      )}
      {done.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{t("learning.my.doneSection")}</h2>
          {done.map(card)}
        </section>
      )}
    </div>
  );
}
