"use client";

/** Levélküldés (158. kör): egyedi e-mail egy címre vagy minden aktív
 *  partnernek, akinél e-mail cím van rögzítve. A cég SMTP-jével megy,
 *  címzettenként külön levélben; alul a kampány-napló. Csak admin. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Recipients {
  count: number;
  sample: { name: string; email: string }[];
  smtp_ready: boolean;
  sender: string | null;
}

interface Campaign {
  id: string;
  subject: string;
  mode: "single" | "partners";
  to_address: string | null;
  recipient_count: number;
  sent_count: number;
  failed_count: number;
  failed: string[];
  status: "running" | "done";
  created_by_name: string;
  created_at: string;
  finished_at: string | null;
}

const inputCls = "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm";

export default function LevelekPage() {
  const { t } = useT();
  const { toast, confirm } = useUI();
  const [rec, setRec] = useState<Recipients | null>(null);
  const [showList, setShowList] = useState(false);
  const [mode, setMode] = useState<"single" | "partners">("single");
  const [to, setTo] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [camps, setCamps] = useState<Campaign[]>([]);

  const load = useCallback(() => {
    api.get<Recipients>("/api/mailing/recipients").then(setRec).catch(() => {});
    api.get<Campaign[]>("/api/mailing/campaigns").then(setCamps).catch(() => {});
  }, []);
  useEffect(load, [load]);

  // futó kampány: pár másodpercenként frissül a napló
  useEffect(() => {
    if (!camps.some((c) => c.status === "running")) return;
    const id = setInterval(() => api.get<Campaign[]>("/api/mailing/campaigns").then(setCamps).catch(() => {}), 4000);
    return () => clearInterval(id);
  }, [camps]);

  async function sendTest() {
    if (!subject.trim() || !body.trim()) { toast(t("mail.fillFirst"), "error"); return; }
    setBusy(true);
    try {
      const r = await api.post<{ to: string }>("/api/mailing/test", { mode: "single", subject, body });
      toast(t("mail.testSent", { to: r.to }));
    } catch (e) { toast(errorMessage(e), "error"); } finally { setBusy(false); }
  }

  async function send() {
    if (!subject.trim() || !body.trim()) { toast(t("mail.fillFirst"), "error"); return; }
    if (mode === "single" && !to.includes("@")) { toast(t("mail.badAddress"), "error"); return; }
    const n = mode === "single" ? to.split(/[;,]/).filter((x) => x.includes("@")).length : (rec?.count ?? 0);
    if (!(await confirm(t("mail.confirm", { n, subject })))) return;
    setBusy(true);
    try {
      const c = await api.post<Campaign>("/api/mailing/send", { mode, to: mode === "single" ? to : null, subject, body });
      toast(c.status === "done" ? t("mail.sentNow", { n: c.sent_count }) : t("mail.queued", { n: c.recipient_count }));
      setSubject(""); setBody(""); if (mode === "single") setTo("");
      load();
    } catch (e) { toast(errorMessage(e), "error"); } finally { setBusy(false); }
  }

  return (
    <AppShell>
      <h1 className="mb-1 text-xl font-bold">✉️ {t("mail.title")}</h1>
      <p className="mb-5 text-sm text-slate-500">{t("mail.hint")}</p>

      {rec && !rec.smtp_ready && (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{t("mail.noSmtp")}</div>
      )}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-3 flex flex-wrap gap-2">
            <button type="button" onClick={() => setMode("single")} className={`rounded-lg px-3 py-1.5 text-sm font-medium ${mode === "single" ? "bg-indigo-600 text-white" : "border border-slate-300 text-slate-700"}`}>
              {t("mail.modeSingle")}
            </button>
            <button type="button" onClick={() => setMode("partners")} className={`rounded-lg px-3 py-1.5 text-sm font-medium ${mode === "partners" ? "bg-indigo-600 text-white" : "border border-slate-300 text-slate-700"}`}>
              {t("mail.modePartners", { n: rec?.count ?? 0 })}
            </button>
          </div>
          {mode === "single" ? (
            <label className="block text-sm">
              {t("mail.to")}
              <input value={to} onChange={(e) => setTo(e.target.value)} placeholder="nev@example.com" className={inputCls} />
              <span className="mt-1 block text-xs text-slate-400">{t("mail.toHint")}</span>
            </label>
          ) : (
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
              {t("mail.partnersInfo", { n: rec?.count ?? 0 })}
              <button type="button" onClick={() => setShowList((v) => !v)} className="ml-2 text-indigo-600 hover:underline">
                {showList ? t("mail.hideList") : t("mail.showList")}
              </button>
              {showList && (
                <ul className="mt-2 max-h-48 overflow-y-auto text-xs text-slate-600">
                  {(rec?.sample ?? []).map((r) => <li key={r.email}>{r.name} · {r.email}</li>)}
                  {(rec?.count ?? 0) > (rec?.sample.length ?? 0) && <li className="text-slate-400">… +{(rec?.count ?? 0) - (rec?.sample.length ?? 0)}</li>}
                </ul>
              )}
            </div>
          )}
          <label className="mt-3 block text-sm">
            {t("mail.subject")}
            <input value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={200} className={inputCls} />
          </label>
          <label className="mt-3 block text-sm">
            {t("mail.body")}
            <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={12} maxLength={20000} className={inputCls} />
            <span className="mt-1 block text-xs text-slate-400">{t("mail.bodyHint")}</span>
          </label>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button type="button" disabled={busy || !rec?.smtp_ready} onClick={send} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
              {mode === "single" ? t("mail.send") : t("mail.sendAll", { n: rec?.count ?? 0 })}
            </button>
            <button type="button" disabled={busy || !rec?.smtp_ready} onClick={sendTest} className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50">
              {t("mail.sendTest")}
            </button>
            {rec?.sender && <span className="text-xs text-slate-400">{t("mail.sender", { from: rec.sender })}</span>}
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 text-base font-semibold">{t("mail.history")}</h2>
          {camps.length === 0 && <p className="text-sm text-slate-400">{t("mail.noHistory")}</p>}
          <ul className="space-y-2">
            {camps.map((c) => (
              <li key={c.id} className="rounded-lg border border-slate-100 p-2 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{c.subject}</span>
                  <span className={`rounded-full px-2 py-0.5 text-xs ${c.status === "done" ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"}`}>
                    {c.status === "done" ? t("mail.done") : t("mail.running")}
                  </span>
                </div>
                <div className="text-xs text-slate-500">
                  {c.created_at.slice(0, 16).replace("T", " ")} · {c.created_by_name} · {c.mode === "single" ? c.to_address : t("mail.allPartners")}
                </div>
                <div className="text-xs text-slate-600">
                  {t("mail.stats", { total: c.recipient_count, sent: c.sent_count, failed: c.failed_count })}
                </div>
                {c.failed.length > 0 && <div className="text-xs text-rose-600">{t("mail.failedList")}: {c.failed.join(", ")}</div>}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </AppShell>
  );
}
