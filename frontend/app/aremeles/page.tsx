"use client";

/** Áremelés / indexálás (184. kör): partner-kör + százalék vagy fix összeg +
 *  kerekítés + hatály → előnézet (régi → új minden érintett árra) → ütemezés.
 *  A hatály napján magától átáll; értesítő levél partnerenként a saját
 *  tételeivel; visszavonás, amíg az árak nem változtak közben. Csak admin. */

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface PartnerRow { id: string; name: string; partner_code: string | null }
interface Row { partner_id: string; partner: string; kind: string; ref: string; label: string; old: number; new: number; applied?: boolean | null }
interface Preview { rows: Row[]; partners: number; items: number; partners_without_email: string[] }
interface Indexation {
  id: string; name: string; percent: number | null; amount: number | null; round_to: number; effective_from: string;
  targets: string[]; partner_ids: string[] | null; status: "scheduled" | "applied" | "reverted" | "cancelled";
  rows: Row[]; partners: number; items: number; notice_sent_at: string | null; notice_count: number;
  applied_at: string | null; reverted_at: string | null; created_by_name: string; created_at: string;
}

const TARGETS = ["partner_price", "below_min", "below_min_kg", "tier", "rent_fee", "counter_price"] as const;
const inputCls = "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm";
const fmt = (n: number) => `${(Math.round(n * 100) / 100).toLocaleString("hu-HU")} Ft`;

function nextMonthFirst(): string {
  const d = new Date();
  const n = new Date(d.getFullYear(), d.getMonth() + 1, 1);
  return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, "0")}-01`;
}

export default function AremelesPage() {
  const { t } = useT();
  const { toast, confirm } = useUI();
  const [partners, setPartners] = useState<PartnerRow[]>([]);
  const [all, setAll] = useState(true);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<"percent" | "amount">("percent");
  const [value, setValue] = useState("5");
  const [roundTo, setRoundTo] = useState(1);
  const [from, setFrom] = useState(nextMonthFirst);
  const [targets, setTargets] = useState<Set<string>>(new Set(["partner_price", "below_min", "below_min_kg", "tier", "rent_fee", "counter_price"]));
  const [name, setName] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [list, setList] = useState<Indexation[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ ix: Indexation; subject: string; body: string; sample: { partner: string; subject: string; body: string } | null } | null>(null);

  const load = useCallback(() => {
    api.get<Indexation[]>("/api/price-indexations").then(setList).catch(() => {});
  }, []);
  useEffect(() => {
    api.get<PartnerRow[]>("/api/partners").then(setPartners).catch(() => {});
    load();
  }, [load]);

  const body = () => ({
    partner_ids: all ? null : Array.from(sel),
    targets: Array.from(targets),
    percent: mode === "percent" ? Number(value) || null : null,
    amount: mode === "amount" ? Number(value) || null : null,
    round_to: roundTo,
    effective_from: from,
  });
  const valid = (Number(value) || 0) !== 0 && targets.size > 0 && (all || sel.size > 0) && !!from;

  async function doPreview() {
    setBusy(true);
    try {
      setPreview(await api.post<Preview>("/api/price-indexations/preview", body()));
    } catch (e) { toast(errorMessage(e), "error"); } finally { setBusy(false); }
  }
  async function schedule() {
    if (!name.trim()) { toast(t("price.nameRequired"), "error"); return; }
    const today = new Date().toISOString().slice(0, 10);
    if (!(await confirm(from <= today ? t("price.confirmNow", { n: preview?.items ?? 0 }) : t("price.confirmSchedule", { n: preview?.items ?? 0, d: from })))) return;
    setBusy(true);
    try {
      const ix = await api.post<Indexation>("/api/price-indexations", { ...body(), name: name.trim() });
      toast(ix.status === "applied" ? t("price.applied") : t("price.scheduled"), "success");
      setPreview(null); setName(""); load();
    } catch (e) { toast(errorMessage(e), "error"); } finally { setBusy(false); }
  }
  async function act(ix: Indexation, action: "apply" | "revert" | "cancel") {
    const msg = action === "apply" ? t("price.confirmApply", { name: ix.name }) : action === "revert" ? t("price.confirmRevert", { name: ix.name }) : t("price.confirmCancel", { name: ix.name });
    if (!(await confirm(msg))) return;
    try {
      if (action === "cancel") await api.delete(`/api/price-indexations/${ix.id}`);
      else await api.post(`/api/price-indexations/${ix.id}/${action}`);
      toast(t("price.done"), "success"); load();
    } catch (e) { toast(errorMessage(e), "error"); }
  }
  function openNotice(ix: Indexation) {
    setNotice({ ix, subject: t("price.noticeDefaultSubject"), body: t("price.noticeDefaultBody"), sample: null });
  }
  async function noticeSample() {
    if (!notice) return;
    try {
      const s = await api.post<{ partner: string; subject: string; body: string }>(`/api/price-indexations/${notice.ix.id}/notice-preview`, { subject: notice.subject, body: notice.body });
      setNotice({ ...notice, sample: s });
    } catch (e) { toast(errorMessage(e), "error"); }
  }
  async function noticeSend() {
    if (!notice) return;
    if (!(await confirm(t("price.confirmNotice", { n: notice.ix.partners })))) return;
    try {
      const r = await api.post<{ recipients: number }>(`/api/price-indexations/${notice.ix.id}/notify`, { subject: notice.subject, body: notice.body });
      toast(t("price.noticeSent", { n: r.recipients }), "success");
      setNotice(null); load();
    } catch (e) { toast(errorMessage(e), "error"); }
  }

  const filtered = useMemo(() => partners.filter((p) => !q || p.name.toLowerCase().includes(q.toLowerCase()) || (p.partner_code ?? "").toLowerCase().includes(q.toLowerCase())), [partners, q]);
  const grouped = (rows: Row[]) => {
    const m = new Map<string, Row[]>();
    rows.forEach((r) => m.set(r.partner, [...(m.get(r.partner) ?? []), r]));
    return Array.from(m.entries());
  };
  const statusCls: Record<Indexation["status"], string> = {
    scheduled: "bg-amber-100 text-amber-800", applied: "bg-emerald-100 text-emerald-800",
    reverted: "bg-slate-200 text-slate-700", cancelled: "bg-slate-100 text-slate-500",
  };
  const measure = (ix: Indexation) => [ix.percent ? `${ix.percent}%` : "", ix.amount ? `+${ix.amount} Ft` : ""].filter(Boolean).join(" + ");

  const rowsTable = (rows: Row[]) => (
    <div className="mt-2 max-h-96 overflow-auto rounded-lg border border-slate-200">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
          <tr><th className="px-3 py-2">{t("price.colPartner")}</th><th className="px-3 py-2">{t("price.colWhat")}</th><th className="px-3 py-2 text-right">{t("price.colOld")}</th><th className="px-3 py-2 text-right">{t("price.colNew")}</th></tr>
        </thead>
        <tbody>
          {grouped(rows).map(([partner, rs]) => rs.map((r, i) => (
            <tr key={`${r.partner_id}-${r.kind}-${r.ref}`} className={`border-t border-slate-100 ${r.applied === false ? "text-slate-400 line-through" : ""}`}>
              <td className="px-3 py-1.5 font-medium">{i === 0 ? partner : ""}</td>
              <td className="px-3 py-1.5">{t(`price.kind.${r.kind}`)} · <span className="text-slate-500">{r.label}</span></td>
              <td className="px-3 py-1.5 text-right tabular-nums">{fmt(r.old)}</td>
              <td className="px-3 py-1.5 text-right font-semibold tabular-nums">{fmt(r.new)}</td>
            </tr>
          )))}
        </tbody>
      </table>
    </div>
  );

  return (
    <AppShell>
      <h1 className="mb-1 text-xl font-bold">🏷️ {t("price.title")}</h1>
      <p className="mb-5 text-sm text-slate-500">{t("price.hint")}</p>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 font-semibold">{t("price.newTitle")}</h2>
          <div className="mb-3 flex flex-wrap gap-2">
            <button type="button" onClick={() => setAll(true)} className={`rounded-lg px-3 py-1.5 text-sm font-medium ${all ? "bg-indigo-600 text-white" : "border border-slate-300 text-slate-700"}`}>{t("price.allPartners", { n: partners.length })}</button>
            <button type="button" onClick={() => setAll(false)} className={`rounded-lg px-3 py-1.5 text-sm font-medium ${!all ? "bg-indigo-600 text-white" : "border border-slate-300 text-slate-700"}`}>{t("price.pickPartners", { n: sel.size })}</button>
          </div>
          {!all && (
            <div className="mb-3 rounded-lg border border-slate-200 p-2">
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("price.search")} className="mb-2 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm" />
              <div className="max-h-48 space-y-1 overflow-auto text-sm">
                {filtered.map((p) => (
                  <label key={p.id} className="flex items-center gap-2">
                    <input type="checkbox" checked={sel.has(p.id)} onChange={(e) => { const n = new Set(sel); if (e.target.checked) n.add(p.id); else n.delete(p.id); setSel(n); }} />
                    {p.name} {p.partner_code && <span className="text-xs text-slate-400">{p.partner_code}</span>}
                  </label>
                ))}
              </div>
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-3">
            <label className="block text-sm">{t("price.measure")}
              <div className="mt-1 flex">
                <select value={mode} onChange={(e) => setMode(e.target.value as "percent" | "amount")} className="flex-none rounded-l-lg border border-slate-300 px-2 py-2 text-sm">
                  <option value="percent">%</option><option value="amount">+ Ft</option>
                </select>
                <input type="number" step="0.1" value={value} onChange={(e) => setValue(e.target.value)} className="min-w-0 flex-1 rounded-r-lg border border-l-0 border-slate-300 px-3 py-2 text-sm" />
              </div>
            </label>
            <label className="block text-sm">{t("price.roundTo")}
              <select value={roundTo} onChange={(e) => setRoundTo(Number(e.target.value))} className={inputCls}>
                <option value={1}>{t("price.roundNone")}</option><option value={5}>5 Ft</option><option value={10}>10 Ft</option><option value={100}>100 Ft</option>
              </select>
            </label>
            <label className="block text-sm">{t("price.effectiveFrom")}<input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={inputCls} /></label>
          </div>
          <div className="mt-3 text-sm">
            <div className="mb-1 text-xs font-semibold uppercase text-slate-500">{t("price.targets")}</div>
            <div className="grid gap-1 sm:grid-cols-2">
              {TARGETS.map((k) => (
                <label key={k} className="flex items-center gap-2">
                  <input type="checkbox" checked={targets.has(k)} onChange={(e) => { const n = new Set(targets); if (e.target.checked) n.add(k); else n.delete(k); setTargets(n); }} />
                  {t(`price.kind.${k}`)}
                </label>
              ))}
            </div>
          </div>
          <div className="mt-4 flex flex-wrap items-end gap-2">
            <button type="button" disabled={!valid || busy} onClick={doPreview} className="rounded-lg border border-indigo-300 px-4 py-2 text-sm font-medium text-indigo-700 disabled:opacity-50">{t("price.preview")}</button>
            {preview && (
              <>
                <label className="block flex-1 text-sm">{t("price.name")}<input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("price.namePh")} className={inputCls} /></label>
                <button type="button" disabled={busy || preview.items === 0} onClick={schedule} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">{t("price.schedule")}</button>
              </>
            )}
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 font-semibold">{t("price.listTitle")}</h2>
          {list.length === 0 && <p className="text-sm text-slate-500">{t("price.empty")}</p>}
          <div className="space-y-3">
            {list.map((ix) => (
              <div key={ix.id} className="rounded-xl border border-slate-200 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold">{ix.name}</span>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${statusCls[ix.status]}`}>{t(`price.status.${ix.status}`)}</span>
                  <span className="text-sm text-slate-500">{measure(ix)} · {t("price.effective")} {ix.effective_from} · {t("price.previewSummary", { p: ix.partners, n: ix.items })}</span>
                </div>
                <div className="mt-1 text-xs text-slate-400">
                  {ix.created_by_name} · {ix.created_at.slice(0, 16).replace("T", " ")}
                  {ix.notice_sent_at && <> · ✉️ {t("price.noticeInfo", { n: ix.notice_count, d: ix.notice_sent_at.slice(0, 10) })}</>}
                  {ix.applied_at && <> · ✅ {ix.applied_at.slice(0, 16).replace("T", " ")}</>}
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  <button type="button" onClick={() => setOpen(open === ix.id ? null : ix.id)} className="rounded-lg border border-slate-300 px-3 py-1 text-xs">{open === ix.id ? t("price.hideRows") : t("price.showRows")}</button>
                  {(ix.status === "scheduled" || ix.status === "applied") && <button type="button" onClick={() => openNotice(ix)} className="rounded-lg border border-slate-300 px-3 py-1 text-xs">✉️ {t("price.notice")}</button>}
                  {ix.status === "scheduled" && <button type="button" onClick={() => act(ix, "apply")} className="rounded-lg border border-emerald-300 px-3 py-1 text-xs text-emerald-700">{t("price.applyNow")}</button>}
                  {ix.status === "scheduled" && <button type="button" onClick={() => act(ix, "cancel")} className="rounded-lg border border-red-300 px-3 py-1 text-xs text-red-700">{t("price.cancel")}</button>}
                  {ix.status === "applied" && <button type="button" onClick={() => act(ix, "revert")} className="rounded-lg border border-red-300 px-3 py-1 text-xs text-red-700">{t("price.revert")}</button>}
                </div>
                {open === ix.id && rowsTable(ix.rows)}
              </div>
            ))}
          </div>
        </div>
      </div>

      {preview && (
        <div className="mt-5 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="text-sm font-medium">{t("price.previewTitle")} — {t("price.previewSummary", { p: preview.partners, n: preview.items })}</div>
          {preview.partners_without_email.length > 0 && (
            <div className="mt-1 text-xs text-amber-700">{t("price.noEmail", { list: preview.partners_without_email.join(", ") })}</div>
          )}
          {preview.items === 0 ? <p className="mt-2 text-sm text-slate-500">{t("price.nothing")}</p> : rowsTable(preview.rows)}
        </div>
      )}

      {notice && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4" onClick={() => setNotice(null)}>
          <div className="max-h-[92vh] w-full max-w-2xl overflow-auto rounded-2xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="font-semibold">✉️ {t("price.noticeTitle", { name: notice.ix.name })}</h3>
            <p className="mt-1 text-xs text-slate-500">{t("price.noticeHint")}</p>
            <label className="mt-3 block text-sm">{t("price.subject")}<input value={notice.subject} onChange={(e) => setNotice({ ...notice, subject: e.target.value, sample: null })} className={inputCls} /></label>
            <label className="mt-3 block text-sm">{t("price.body")}<textarea rows={10} value={notice.body} onChange={(e) => setNotice({ ...notice, body: e.target.value, sample: null })} className={inputCls} /></label>
            {notice.sample && (
              <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
                <div className="text-xs text-slate-500">{t("price.sampleFor", { partner: notice.sample.partner })} · {notice.sample.subject}</div>
                <pre className="mt-1 whitespace-pre-wrap font-sans">{notice.sample.body}</pre>
              </div>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" onClick={() => setNotice(null)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm">{t("price.close")}</button>
              <button type="button" onClick={noticeSample} className="rounded-lg border border-indigo-300 px-3 py-1.5 text-sm text-indigo-700">{t("price.sample")}</button>
              <button type="button" onClick={noticeSend} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white">{t("price.send", { n: notice.ix.partners })}</button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
