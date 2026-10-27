"use client";

/** Saját alkatrészeim (parts modul, B fázis — 1d553d2e): a szerelő saját
 * autós alkatrész-raktárának készlete (min.-jelzéssel), kiadás-kérés a
 * központi raktárból (a raktáros helykódról kiadja → a szerelő nyugtázza),
 * a kérések állapota. A központi raktárhoz és más autókhoz nem lát. */

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import SearchSelect from "@/components/SearchSelect";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface StockRow { part_id: string; sku: string; name: string; unit: string; quantity: number; min_stock: number; total_qty: number; low: boolean }
interface CatalogItem { id: string; sku: string; name: string; unit: string }
interface Req {
  id: string; part_id: string; sku: string; name: string; unit: string; quantity: number; note: string | null;
  status: "pending" | "issued" | "received" | "declined" | "cancelled";
  decided_by_name: string | null; decided_at: string | null; decision_note: string | null;
  issued_from_name: string | null; issued_from_location: string | null; issued_quantity: number | null;
  received_at: string | null; created_at: string;
}
interface Replenish { part_id: string; sku: string; name: string; unit: string; min_qty: number; max_qty: number; quantity: number; suggested: number }
interface MyWarehouse { warehouse_id: string | null; warehouse_name: string | null; stock: StockRow[]; requests: Req[]; catalog: CatalogItem[] }

const REQ_CHIP: Record<Req["status"], string> = {
  pending: "bg-amber-100 text-amber-800",
  issued: "bg-sky-100 text-sky-800",
  received: "bg-emerald-100 text-emerald-800",
  declined: "bg-rose-100 text-rose-800",
  cancelled: "bg-slate-100 text-slate-500",
};
const REQ_ICON: Record<Req["status"], string> = { pending: "⏳", issued: "📤", received: "✅", declined: "🚫", cancelled: "↩️" };

export default function SajatAlkatreszekPage() {
  const { t, lang } = useT();
  const { toast, confirm } = useUI();
  const fmt = (dt: string) => new Date(dt).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "short", timeStyle: "short" });
  const [wh, setWh] = useState<MyWarehouse | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<{ part_id: string; quantity: string; note: string } | null>(null);
  const [count, setCount] = useState<Record<string, string> | null>(null);
  const [replenish, setReplenish] = useState<Replenish[]>([]);

  const load = useCallback(() => {
    api.get<MyWarehouse>("/api/parts/my-warehouse").then(setWh).catch((e) => toast(errorMessage(e), "error"));
    api.get<Replenish[]>("/api/parts/my-replenish").then(setReplenish).catch(() => setReplenish([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { load(); }, [load]);

  async function createPartInline(name: string) {
    try {
      const p = await api.post<{ id: string; name: string; sku: string }>("/api/parts", { name, unit: "db" });
      load();
      setForm((f) => (f ? { ...f, part_id: p.id } : f));
      toast(t("parts.createdInline", { name: p.name, sku: p.sku }), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }
  const catalogItems = useMemo(
    () => (wh?.catalog ?? []).map((c) => ({ id: c.id, label: c.name, badge: c.sku, sublabel: c.unit })),
    [wh],
  );

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!form || !form.part_id) return;
    setBusy(true);
    try {
      await api.post("/api/parts/requests", { part_id: form.part_id, quantity: Number(form.quantity.replace(",", ".")) || 0, note: form.note || null });
      toast(t("myParts.sent"), "success");
      setForm(null);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function requestAll() {
    if (replenish.length === 0) return;
    setBusy(true);
    try {
      const r = await api.post<{ created: number }>("/api/parts/requests/bulk", { items: replenish.map((x) => ({ part_id: x.part_id, quantity: x.suggested })) });
      toast(t("myParts.requested", { n: r.created }), "success");
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function submitCount() {
    if (!count || !wh) return;
    setBusy(true);
    try {
      const counts = wh.stock.map((s) => ({ part_id: s.part_id, quantity: Number((count[s.part_id] ?? String(s.quantity)).replace(",", ".")) || 0 }));
      const res = await api.post<{ adjusted: number }>("/api/parts/my-stocktake", { counts });
      toast(t("myParts.countDone", { n: res.adjusted }), "success");
      setCount(null);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function act(r: Req, action: "receive" | "cancel") {
    if (action === "cancel" && !(await confirm(t("myParts.cancel") + "?"))) return;
    try {
      await api.post(`/api/parts/requests/${r.id}/${action}`, {});
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  const minPill = (s: StockRow) => {
    if (s.quantity <= 0) return <span className="rounded-full bg-rose-100 px-2 py-0.5 text-[11px] font-semibold text-rose-800">{t("myParts.min.out")}</span>;
    if (s.low) return <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-800">{t("myParts.min.low")}</span>;
    return <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-semibold text-emerald-800">{t("myParts.min.ok")}</span>;
  };

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-xl font-semibold">🔩 {t("myParts.title")} {wh?.warehouse_name && <span className="ml-1 rounded-full bg-slate-100 px-2 py-0.5 text-sm font-normal text-slate-600">🚚 {wh.warehouse_name}</span>}</h1>
          <p className="text-sm text-slate-500">{t("myParts.subtitle")}</p>
        </div>
        {wh?.warehouse_id && (
          <div className="ml-auto flex gap-2">
            <button onClick={() => setCount(Object.fromEntries(wh.stock.map((s) => [s.part_id, String(s.quantity)])))} disabled={wh.stock.length === 0} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-100 disabled:opacity-50">{t("myParts.count")}</button>
            <button onClick={() => setForm({ part_id: "", quantity: "1", note: "" })} className="rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700">{t("myParts.newRequest")}</button>
          </div>
        )}
      </div>

      {wh && !wh.warehouse_id && (
        <p className="rounded-2xl border border-amber-200 bg-amber-50 p-6 text-amber-800">{t("myParts.noVehicle")}</p>
      )}

      {wh?.warehouse_id && (
        <div className="grid gap-4 lg:grid-cols-2">
          {/* ── Készlet az autón ── */}
          <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="mb-2 font-semibold">{t("myParts.stock")}</h2>
            {wh.stock.length === 0 ? <p className="py-6 text-center text-sm text-slate-400">{t("myParts.emptyStock")}</p> : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                    <th className="px-2 py-1.5">{t("parts.sku")}</th><th className="px-2 py-1.5">{t("parts.name")}</th><th className="px-2 py-1.5 text-right">{t("parts.quantity")}</th><th className="px-2 py-1.5">Min.</th><th className="px-2 py-1.5"></th>
                  </tr></thead>
                  <tbody>
                    {wh.stock.map((s) => (
                      <tr key={s.part_id} className="border-b border-slate-100">
                        <td className="px-2 py-1.5 font-mono text-xs text-slate-500">{s.sku}</td>
                        <td className="px-2 py-1.5">{s.name}<span className="ml-1 text-[11px] text-slate-400">· {t("myParts.companyTotal")}: {s.total_qty} {s.unit}</span></td>
                        <td className="px-2 py-1.5 text-right tabular-nums font-medium">{s.quantity} {s.unit}</td>
                        <td className="px-2 py-1.5">{minPill(s)}</td>
                        <td className="px-2 py-1.5 text-right">
                          <button onClick={() => setForm({ part_id: s.part_id, quantity: "1", note: "" })} className={`rounded-lg border px-2 py-0.5 text-xs ${s.low ? "border-indigo-600 bg-indigo-600 text-white hover:bg-indigo-700" : "border-slate-300 hover:bg-slate-100"}`}>{t("myParts.request")}</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* ── Feltöltőlista (autónkénti min/max) ── */}
          <section className="rounded-2xl border border-indigo-200 bg-white p-4 shadow-sm lg:col-span-2">
            <div className="mb-1 flex items-center gap-3">
              <h2 className="font-semibold">{t("myParts.replenish")}</h2>
              <span className="text-[11px] text-slate-500">{t("myParts.replenishHint")}</span>
              {replenish.length > 0 && <button disabled={busy} onClick={() => void requestAll()} className="ml-auto rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">{t("myParts.requestAll")} ({replenish.length})</button>}
            </div>
            {replenish.length === 0 ? <p className="py-3 text-center text-sm text-slate-400">{t("myParts.replenishEmpty")}</p> : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500"><th className="px-2 py-1.5">{t("parts.sku")}</th><th className="px-2 py-1.5">{t("parts.name")}</th><th className="px-2 py-1.5 text-right">{t("parts.quantity")}</th><th className="px-2 py-1.5 text-right">{t("parts.levelMin")} / {t("parts.levelMax")}</th><th className="px-2 py-1.5 text-right">{t("myParts.suggested")}</th></tr></thead>
                  <tbody>{replenish.map((r) => (
                    <tr key={r.part_id} className="border-b border-slate-100"><td className="px-2 py-1.5 font-mono text-xs text-slate-500">{r.sku}</td><td className="px-2 py-1.5">{r.name}</td><td className="px-2 py-1.5 text-right tabular-nums text-rose-700">{r.quantity} {r.unit}</td><td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{r.min_qty} / {r.max_qty}</td><td className="px-2 py-1.5 text-right tabular-nums font-semibold">{r.suggested} {r.unit}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </section>

          {/* ── Kiadás-kérések ── */}
          <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="mb-1 font-semibold">{t("myParts.requests")}</h2>
            <p className="mb-3 text-[11px] text-slate-500">{t("myParts.steps")}</p>
            {wh.requests.length === 0 ? <p className="py-6 text-center text-sm text-slate-400">{t("myParts.emptyRequests")}</p> : (
              <div className="space-y-2">
                {wh.requests.map((r) => (
                  <div key={r.id} className="flex items-center gap-3 rounded-xl border border-slate-200 px-3 py-2 text-sm">
                    <span className="text-lg">{REQ_ICON[r.status]}</span>
                    <div className="min-w-0 flex-1">
                      <div className="font-medium">{r.name} · {r.quantity} {r.unit}</div>
                      <div className="text-[11px] text-slate-500">
                        {fmt(r.created_at)}{r.note && ` · „${r.note}”`}
                        {r.status === "issued" && r.issued_from_name && ` · ${t("parts.req.issuedFrom").toLowerCase()}: ${r.issued_from_name}${r.issued_from_location ? " " + r.issued_from_location : ""}`}
                        {r.status === "declined" && r.decision_note && ` · ${r.decision_note}`}
                        {r.decided_by_name && ` · ${r.decided_by_name}`}
                      </div>
                    </div>
                    {r.status === "issued" ? (
                      <button onClick={() => void act(r, "receive")} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700">{t("myParts.received")}</button>
                    ) : r.status === "pending" ? (
                      <span className="flex items-center gap-1.5">
                        <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${REQ_CHIP[r.status]}`}>{t(`parts.req.${r.status}`)}</span>
                        <button onClick={() => void act(r, "cancel")} className="rounded-lg border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100">{t("myParts.cancel")}</button>
                      </span>
                    ) : (
                      <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${REQ_CHIP[r.status]}`}>{t(`parts.req.${r.status}`)}</span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      )}

      {/* ── Számoltam (leltár az autón) modal ── */}
      {count && wh && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="relative max-h-[90vh] w-full max-w-md space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setCount(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">{t("myParts.countTitle")}</h2>
            <p className="text-xs text-slate-500">{t("myParts.countHint")}</p>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <tbody>
                  {wh.stock.map((s) => {
                    const v = Number((count[s.part_id] ?? "").replace(",", "."));
                    const diff = Number.isFinite(v) ? v - s.quantity : 0;
                    return (
                      <tr key={s.part_id} className="border-b border-slate-100">
                        <td className="py-1.5 pr-2"><span className="font-mono text-[11px] text-slate-400">{s.sku}</span> {s.name}<span className="block text-[11px] text-slate-400">{s.quantity} {s.unit}</span></td>
                        <td className="py-1.5 text-right">
                          <input type="number" min={0} step="any" value={count[s.part_id] ?? ""} onChange={(e) => setCount({ ...count, [s.part_id]: e.target.value })} className={`w-24 rounded-lg border px-2 py-1 text-right ${diff !== 0 ? "border-amber-400" : "border-slate-300"}`} />
                          {diff !== 0 && <span className={`ml-1 text-xs font-semibold ${diff > 0 ? "text-emerald-700" : "text-rose-700"}`}>{diff > 0 ? "+" : ""}{diff}</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setCount(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} onClick={() => void submitCount()} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{busy ? t("common.saving") : t("myParts.count")}</button>
            </div>
          </div>
        </div>
      )}

      {/* ── Kérés modal ── */}
      {form && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={submit} className="relative w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setForm(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">🔁 {t("myParts.newRequest")}</h2>
            <div className="text-sm">
              <p className="mb-1 text-xs text-slate-500">{t("myParts.part")} *</p>
              <SearchSelect
                items={catalogItems}
                value={form.part_id}
                onChange={(id) => setForm({ ...form, part_id: id })}
                placeholder={t("parts.search")}
                createLabel={t("parts.createInline")}
                onCreate={(name) => void createPartInline(name)}
              />
            </div>
            <label className="block text-sm">{t("myParts.quantity")} *<input required type="number" min={0} step="any" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" /></label>
            <label className="block text-sm">{t("myParts.note")}<input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" /></label>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy || !form.part_id} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{busy ? t("common.saving") : t("common.save")}</button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
