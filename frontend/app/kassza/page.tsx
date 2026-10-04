"use client";

/** Kassza: minden dolgozónak saját készpénz-kasszája van — ide folyik be a
 * készpénzes elszámolás-bevétel, itt rögzíti a költségeit (összeg,
 * megnevezés, beszállító, bizonylatszám). A manager/admin (invoicing jog)
 * betétet/kivétet rögzíthet bármely kasszába, és látja a központi
 * összesítést dolgozónként + cégenként (készpénz jól elhatárolva). */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { COMPANY_SHORT } from "@/lib/companies";
import { useT } from "@/lib/i18n";
import { usePerms } from "@/lib/perms";
import { useUI } from "@/lib/ui";

interface CashEntry {
  id: string;
  expense_date: string;
  amount_gross: number;
  entry_type: "expense" | "deposit" | "withdrawal";
  note: string | null;
  supplier: string | null;
  receipt_no: string | null;
  created_by_name: string | null;
}

interface Register {
  user_id: string;
  user_name: string | null;
  cash_revenue: number;
  deposits: number;
  withdrawals: number;
  expenses: number;
  balance: number;
  entries: CashEntry[];
}

interface Overview {
  registers: Register[];
  total_balance: number;
  by_company: Record<string, { cash: number; other: number; count: number }>;
  by_payment: Record<string, { gross: number; count: number }>;
}

const ENTRY_CHIP: Record<string, string> = {
  expense: "bg-rose-100 text-rose-800",
  deposit: "bg-emerald-100 text-emerald-800",
  withdrawal: "bg-amber-100 text-amber-800",
};

export default function KasszaPage() {
  const { t, lang } = useT();
  const { toast } = useUI();
  const canManage = usePerms().can("invoicing");
  const ft = (n: number) => `${n.toLocaleString(lang === "hu" ? "hu-HU" : "en-GB")} Ft`;

  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [mine, setMine] = useState<Register | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [drillUser, setDrillUser] = useState<string>("");
  const [drill, setDrill] = useState<Register | null>(null);

  // Tétel-űrlap: saját költség, vagy (managerként) betét/kivét másnak
  const [form, setForm] = useState<{
    entry_type: "expense" | "deposit" | "withdrawal";
    amount: string; note: string; supplier: string; receipt_no: string;
    user_id: string;
  } | null>(null);
  const [busy, setBusy] = useState(false);

  const params = useCallback(() => {
    const p = new URLSearchParams();
    if (dateFrom) p.set("date_from", dateFrom);
    if (dateTo) p.set("date_to", dateTo);
    return p;
  }, [dateFrom, dateTo]);

  const load = useCallback(() => {
    api.get<Register>(`/api/stats/cash/me?${params()}`).then(setMine).catch(() => {});
    if (canManage) {
      api.get<Overview>(`/api/stats/cash/overview?${params()}`).then(setOverview).catch(() => {});
    }
  }, [params, canManage]);
  useEffect(load, [load]);

  useEffect(() => {
    if (!drillUser) { setDrill(null); return; }
    const p = params();
    p.set("user_id", drillUser);
    api.get<Overview>(`/api/stats/cash/overview?${p}`)
      .then((o) => setDrill(o.registers[0] ?? null))
      .catch(() => {});
  }, [drillUser, params]);

  async function saveEntry(e: React.FormEvent) {
    e.preventDefault();
    if (!form) return;
    setBusy(true);
    try {
      await api.post("/api/settlements/expenses", {
        amount_gross: Number(form.amount),
        note: form.note || null,
        supplier: form.supplier || null,
        receipt_no: form.receipt_no || null,
        entry_type: form.entry_type,
        user_id: form.user_id || null,
      });
      toast(t("cash.entrySaved"), "success");
      setForm(null);
      load();
      if (drillUser) setDrillUser((u) => u); // frissítés
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  function registerCard(r: Register, title: string) {
    return (
      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-semibold">💰 {title}</h2>
          <span className={`rounded-lg px-3 py-1 text-lg font-bold ${r.balance >= 0 ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"}`}>
            {ft(r.balance)}
          </span>
        </div>
        <div className="mb-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
          <div className="rounded-xl bg-slate-50 px-3 py-2">
            <p className="text-xs text-slate-500">{t("cash.revenue")}</p>
            <p className="font-semibold">{ft(r.cash_revenue)}</p>
          </div>
          <div className="rounded-xl bg-emerald-50 px-3 py-2">
            <p className="text-xs text-emerald-700">{t("cash.deposits")}</p>
            <p className="font-semibold text-emerald-800">+{ft(r.deposits)}</p>
          </div>
          <div className="rounded-xl bg-amber-50 px-3 py-2">
            <p className="text-xs text-amber-700">{t("cash.withdrawals")}</p>
            <p className="font-semibold text-amber-800">−{ft(r.withdrawals)}</p>
          </div>
          <div className="rounded-xl bg-rose-50 px-3 py-2">
            <p className="text-xs text-rose-700">{t("cash.expenses")}</p>
            <p className="font-semibold text-rose-800">−{ft(r.expenses)}</p>
          </div>
        </div>
        {r.entries.length > 0 && (
          <div className="max-h-80 overflow-y-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-slate-500">
                  <th className="py-1.5 pr-2">{t("cash.date")}</th>
                  <th className="py-1.5 pr-2">{t("cash.type")}</th>
                  <th className="py-1.5 pr-2 text-right">{t("cash.amount")}</th>
                  <th className="py-1.5 pr-2">{t("cash.details")}</th>
                </tr>
              </thead>
              <tbody>
                {r.entries.map((e) => (
                  <tr key={e.id} className="border-t border-slate-100">
                    <td className="py-1.5 pr-2 whitespace-nowrap">{e.expense_date}</td>
                    <td className="py-1.5 pr-2">
                      <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${ENTRY_CHIP[e.entry_type]}`}>
                        {t(`cash.types.${e.entry_type}`)}
                      </span>
                    </td>
                    <td className={`py-1.5 pr-2 text-right font-medium tabular-nums ${e.entry_type === "deposit" ? "text-emerald-700" : "text-rose-700"}`}>
                      {e.entry_type === "deposit" ? "+" : "−"}{ft(e.amount_gross)}
                    </td>
                    <td className="py-1.5 pr-2 text-xs text-slate-500">
                      {[e.note, e.supplier, e.receipt_no && `#${e.receipt_no}`, e.created_by_name && `(${e.created_by_name})`]
                        .filter(Boolean)
                        .join(" · ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
  }

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">💰 {t("cash.title")}</h1>
        <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        <span className="text-slate-400">–</span>
        <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        <button
          onClick={() => setForm({ entry_type: "expense", amount: "", note: "", supplier: "", receipt_no: "", user_id: "" })}
          className="rounded-lg bg-rose-600 px-4 py-2 text-sm font-medium text-white hover:bg-rose-700"
        >
          − {t("cash.addExpense")}
        </button>
        {canManage && (
          <>
            <button
              onClick={() => setForm({ entry_type: "deposit", amount: "", note: "", supplier: "", receipt_no: "", user_id: "" })}
              className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700"
            >
              + {t("cash.addDeposit")}
            </button>
            <button
              onClick={() => setForm({ entry_type: "withdrawal", amount: "", note: "", supplier: "", receipt_no: "", user_id: "" })}
              className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700"
            >
              − {t("cash.addWithdrawal")}
            </button>
          </>
        )}
      </div>

      <div className="space-y-4">
        {mine && registerCard(mine, t("cash.myRegister"))}

        {canManage && overview && (
          <div className="rounded-2xl border border-indigo-200 bg-white p-5 shadow-sm">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-semibold">🏦 {t("cash.central")}</h2>
              <span className="rounded-lg bg-indigo-50 px-3 py-1 text-lg font-bold text-indigo-700">
                {ft(overview.total_balance)}
              </span>
            </div>
            <div className="mb-3 flex flex-wrap gap-2 text-sm">
              {Object.entries(overview.by_company).map(([c, v]) => (
                <span key={c} className="rounded-xl bg-slate-50 px-3 py-2">
                  <b>{c === "none" ? "—" : COMPANY_SHORT[c as "xp" | "pc"]}</b>:{" "}
                  💵 {ft(v.cash)} <span className="text-slate-400">·</span>{" "}
                  <span className="text-slate-500">{t("cash.otherPayments")}: {ft(v.other)}</span>
                </span>
              ))}
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-slate-500">
                  <th className="py-1.5 pr-2">{t("cash.worker")}</th>
                  <th className="py-1.5 pr-2 text-right">{t("cash.revenue")}</th>
                  <th className="py-1.5 pr-2 text-right">{t("cash.deposits")}</th>
                  <th className="py-1.5 pr-2 text-right">{t("cash.withdrawals")}</th>
                  <th className="py-1.5 pr-2 text-right">{t("cash.expenses")}</th>
                  <th className="py-1.5 pr-2 text-right">{t("cash.balance")}</th>
                </tr>
              </thead>
              <tbody>
                {overview.registers.map((r) => (
                  <tr key={r.user_id} className="border-t border-slate-100">
                    <td className="py-1.5 pr-2">
                      <button
                        onClick={() => setDrillUser(drillUser === r.user_id ? "" : r.user_id)}
                        className="font-medium text-indigo-700 hover:underline"
                      >
                        {r.user_name ?? r.user_id.slice(0, 8)}
                      </button>
                    </td>
                    <td className="py-1.5 pr-2 text-right tabular-nums">{ft(r.cash_revenue)}</td>
                    <td className="py-1.5 pr-2 text-right tabular-nums text-emerald-700">+{ft(r.deposits)}</td>
                    <td className="py-1.5 pr-2 text-right tabular-nums text-amber-700">−{ft(r.withdrawals)}</td>
                    <td className="py-1.5 pr-2 text-right tabular-nums text-rose-700">−{ft(r.expenses)}</td>
                    <td className={`py-1.5 pr-2 text-right font-semibold tabular-nums ${r.balance >= 0 ? "text-emerald-700" : "text-rose-700"}`}>
                      {ft(r.balance)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {drill && registerCard(drill, `${drill.user_name ?? "?"} — ${t("cash.title")}`)}
      </div>

      {form && (
        <div onMouseDown={(e) => { if (e.target === e.currentTarget) setForm(null); }} className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={saveEntry} className="w-full max-w-sm space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">
              {t(`cash.formTitles.${form.entry_type}`)}
            </h2>
            {canManage && form.entry_type !== "expense" && overview && (
              <label className="block text-sm">
                {t("cash.worker")}
                <select
                  required
                  value={form.user_id}
                  onChange={(e) => setForm({ ...form, user_id: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2"
                >
                  <option value="">{t("tasks.choose")}</option>
                  {overview.registers.map((r) => (
                    <option key={r.user_id} value={r.user_id}>{r.user_name ?? r.user_id.slice(0, 8)}</option>
                  ))}
                </select>
              </label>
            )}
            <label className="block text-sm">
              {t("cash.amount")} (Ft) *
              <input required type="number" min={1} value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              {t("cash.note")} *
              <input required value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            {form.entry_type === "expense" && (
              <>
                <label className="block text-sm">
                  {t("cash.supplier")}
                  <input value={form.supplier} onChange={(e) => setForm({ ...form, supplier: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
                </label>
                <label className="block text-sm">
                  {t("cash.receiptNo")}
                  <input value={form.receipt_no} onChange={(e) => setForm({ ...form, receipt_no: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
                </label>
              </>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
                {busy ? t("common.saving") : t("common.save")}
              </button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
