"use client";

/** X-Learning – egy kiosztott tananyag: szöveg, beágyazott YouTube-videó,
 *  csatolmányok, teszt (feleletválasztós, több helyes is), eredmény, oklevél.
 *  Csak a saját kiosztás nyitható meg (idegen azonosítóra a backend 404-et ad). */

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { API_URL, ApiError, api, errorMessage, printFile } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { STATUS_CLS, fmtSize, renderMarkdown, type AttachmentMeta, type MyItem } from "../shared";

interface Question {
  id: string;
  text: string;
  options: string[];
  multi: boolean;
}

interface Detail {
  assignment: MyItem;
  material: {
    title: string;
    category: string | null;
    body: string | null;
    video_id: string | null;
    attachments: AttachmentMeta[];
    validity_months: number | null;
  };
  questions: Question[];
  attempts: { created_at: string; score_percent: number; passed: boolean }[];
}

interface AttemptResult {
  score_percent: number;
  passed: boolean;
  correct: number;
  total: number;
  status: MyItem["status"];
  valid_until: string | null;
}

export default function LearningMaterialPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { t, lang } = useT();
  const [data, setData] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Record<string, number[]>>({});
  const [result, setResult] = useState<AttemptResult | null>(null);
  const [busy, setBusy] = useState(false);
  // 153. kör (883d89d0): az oklevél az alkalmazáson belül nyílik (új ablak
  // helyett — telefonos alkalmazásból / felugró-tiltással az nem nyílt meg)
  const [cert, setCert] = useState<{ url: string; name: string } | null>(null);
  const [certBusy, setCertBusy] = useState(false);
  async function openCertificate() {
    setCertBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/api/learning/my/${id}/certificate.pdf?inline=1`, { credentials: "include" });
      if (!res.ok) throw new ApiError(res.status, `http.${res.status}`, null);
      const blob = await res.blob();
      const cd = res.headers.get("content-disposition") ?? "";
      const m = /filename\*?=(?:UTF-8'')?"?([^";]+)/i.exec(cd);
      setCert({ url: URL.createObjectURL(blob), name: decodeURIComponent(m?.[1] ?? "oklevel.pdf") });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setCertBusy(false);
    }
  }
  const fd = (d: string | null) => (d ? new Date(d).toLocaleDateString(lang === "hu" ? "hu-HU" : "en-GB") : "—");
  const fdt = (d: string) => new Date(d).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "short", timeStyle: "short" });

  const load = useCallback(() => {
    api
      .get<Detail>(`/api/learning/my/${id}`)
      .then((d) => {
        setData(d);
        setAnswers({});
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) router.replace("/learning/login");
        else setError(errorMessage(err));
      });
  }, [id, router]);
  useEffect(load, [load]);

  function toggle(q: Question, idx: number) {
    setAnswers((prev) => {
      const cur = prev[q.id] ?? [];
      if (!q.multi) return { ...prev, [q.id]: [idx] };
      return { ...prev, [q.id]: cur.includes(idx) ? cur.filter((i) => i !== idx) : [...cur, idx].sort() };
    });
  }

  async function submit() {
    if (!data) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<AttemptResult>(`/api/learning/my/${id}/attempt`, { answers });
      setResult(r);
      // friss állapot + (véletlen kérdéssornál) új kérdések
      api.get<Detail>(`/api/learning/my/${id}`).then((d) => {
        setData(d);
        setAnswers({});
      }).catch(() => {});
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (error && !data) return <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">{error}</div>;
  if (!data) return <p className="text-sm text-slate-500">…</p>;

  const a = data.assignment;
  const m = data.material;
  const allAnswered = data.questions.length > 0 && data.questions.every((q) => (answers[q.id] ?? []).length > 0);
  const isDone = a.status === "done";

  return (
    <div className="space-y-5">
      <Link href="/learning" className="text-sm text-slate-500 hover:underline">
        ← {t("learning.detail.back")}
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold">{m.title}</h1>
          {m.category && <div className="text-sm text-slate-500">{m.category}</div>}
        </div>
        <span className={`rounded-full px-3 py-1 text-xs font-medium ${STATUS_CLS[a.status]}`}>{t(`learning.status.${a.status}`)}</span>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
        {a.due_date && (
          <span>
            {t("learning.my.due")}: <b>{fd(a.due_date)}</b>
          </span>
        )}
        {a.completed_at && (
          <span>
            {t("learning.detail.completed")}: <b>{fd(a.completed_at)}</b>
          </span>
        )}
        {a.valid_until && (
          <span>
            {t("learning.my.validUntil")}: <b>{fd(a.valid_until)}</b>
          </span>
        )}
        {m.validity_months && !a.valid_until && <span>{t("learning.detail.validity", { n: m.validity_months })}</span>}
      </div>

      {m.video_id && (
        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-black shadow-sm">
          <div className="relative w-full" style={{ paddingTop: "56.25%" }}>
            <iframe
              className="absolute inset-0 h-full w-full"
              src={`https://www.youtube-nocookie.com/embed/${m.video_id}`}
              title={m.title}
              // a dokumentum Referrer-Policy: no-referrer (biztonsági audit) — a YouTube
              // referrer nélkül 153-as hibát ad, ezért az iframe saját szabályt kap
              referrerPolicy="strict-origin-when-cross-origin"
              allow="accelerometer; encrypted-media; gyroscope; picture-in-picture"
              allowFullScreen
            />
          </div>
        </section>
      )}

      {m.body && (
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          {renderMarkdown(m.body, (ref) => (m.attachments.some((a) => a.id === ref) ? `/api/learning/my/${id}/attachments/${ref}` : null))}
        </section>
      )}

      {m.attachments.length > 0 && (
        <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="mb-2 text-sm font-semibold">{t("learning.detail.attachments")}</h2>
          <ul className="space-y-1 text-sm">
            {m.attachments.map((att) => (
              <li key={att.id}>
                <a
                  href={`/api/learning/my/${id}/attachments/${att.id}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sky-700 underline"
                >
                  {att.mime === "application/pdf" ? "📄" : "🖼️"} {att.filename}
                </a>{" "}
                <span className="text-xs text-slate-400">({fmtSize(att.size)})</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-lg font-semibold">{t("learning.detail.test")}</h2>
        {isDone ? (
          /* 100 %-os teszt: nincs újrapróbálás, a kérdések eltűnnek — oklevél megtekintés + nyomtatás */
          <div className="mt-3 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
            <div className="font-semibold">{t("learning.detail.passed")}</div>
            <div>{t("learning.detail.doneInfo", { date: fd(a.completed_at), attempts: data.attempts.length })}</div>
            {a.valid_until && (
              <div className="text-xs">
                {t("learning.my.validUntil")}: {fd(a.valid_until)}
              </div>
            )}
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={certBusy}
                onClick={() => void openCertificate()}
                className="rounded-lg bg-emerald-700 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-800 disabled:opacity-50"
              >
                🎓 {certBusy ? t("learning.detail.certLoading") : t("learning.detail.viewCertificate")}
              </button>
              <button
                type="button"
                onClick={() => printFile(`/api/learning/my/${id}/certificate.pdf?inline=1`).catch((err) => setError(errorMessage(err)))}
                className="rounded-lg border border-emerald-700 px-4 py-2 text-sm font-medium text-emerald-800 hover:bg-emerald-100"
              >
                🖨️ {t("learning.detail.printCertificate")}
              </button>
            </div>
            {error && <p className="mt-2 text-sm text-rose-600">{error}</p>}
            {cert && (
              <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-3">
                <div className="flex h-[92vh] w-full max-w-3xl flex-col rounded-2xl bg-white shadow-xl">
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-2">
                    <span className="font-semibold">🎓 {t("learning.detail.viewCertificate")}</span>
                    <div className="flex flex-wrap items-center gap-2">
                      <a href={cert.url} download={cert.name} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100">
                        ⬇ {t("learning.detail.certDownload")}
                      </a>
                      <a href={cert.url} target="_blank" rel="noopener" className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100">
                        ↗ {t("learning.detail.certOpenTab")}
                      </a>
                      <button type="button" onClick={() => { URL.revokeObjectURL(cert.url); setCert(null); }} className="rounded-lg px-2 py-1 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label={t("common.close")}>
                        ✕
                      </button>
                    </div>
                  </div>
                  <iframe src={cert.url} title={t("learning.detail.viewCertificate")} className="min-h-0 flex-1 rounded-b-2xl" />
                  <p className="border-t border-slate-100 px-4 py-1.5 text-xs text-slate-500">{t("learning.detail.certMobileHint")}</p>
                </div>
              </div>
            )}
          </div>
        ) : data.questions.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">{t("learning.detail.noTest")}</p>
        ) : (
          <>
            <p className="mt-1 text-xs text-slate-500">{a.status === "expired" ? t("learning.detail.expiredHint") : t("learning.detail.testHint")}</p>
            {result && !result.passed && (
              <div className="mt-3 rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-900">
                <div className="font-semibold">{t("learning.detail.failed")}</div>
                <div>{t("learning.detail.result", { correct: result.correct, total: result.total, score: result.score_percent })}</div>
              </div>
            )}
            <ol className="mt-4 space-y-4">
              {data.questions.map((q, qi) => (
                <li key={q.id} className="rounded-xl border border-slate-200 p-3">
                  <div className="text-sm font-medium">
                    {qi + 1}. {q.text}
                    {q.multi && <span className="ml-2 text-xs font-normal text-slate-400">({t("learning.detail.multi")})</span>}
                  </div>
                  <div className="mt-2 space-y-1">
                    {q.options.map((opt, oi) => {
                      const checked = (answers[q.id] ?? []).includes(oi);
                      return (
                        <label key={oi} className={`flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1 text-sm ${checked ? "bg-slate-100" : "hover:bg-slate-50"}`}>
                          <input
                            type={q.multi ? "checkbox" : "radio"}
                            name={`q-${q.id}`}
                            checked={checked}
                            onChange={() => toggle(q, oi)}
                          />
                          <span>{opt}</span>
                        </label>
                      );
                    })}
                  </div>
                </li>
              ))}
            </ol>
            {error && <p className="mt-2 text-sm text-rose-600">{error}</p>}
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={!allAnswered || busy}
                onClick={submit}
                className="rounded-lg bg-[#E31E24] px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                {t("learning.detail.submit")}
              </button>
              {!allAnswered && <span className="text-xs text-slate-400">{t("learning.detail.answerAll")}</span>}
            </div>
          </>
        )}
      </section>

      {data.attempts.length > 0 && (
        <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="mb-2 text-sm font-semibold">{t("learning.detail.history")}</h2>
          <ul className="space-y-1 text-sm">
            {data.attempts.map((x, i) => (
              <li key={i} className="flex items-center justify-between">
                <span className="text-slate-600">{fdt(x.created_at)}</span>
                <span className={x.passed ? "font-medium text-emerald-700" : "text-rose-700"}>{x.score_percent} %</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
