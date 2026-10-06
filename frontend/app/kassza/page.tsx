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

interface ContractorLedger {
  fee_total: number;
  costs: number;
  payouts: number;
  balance: number;
  worksheets: number;
}

interface Register {
  user_id: string;
  user_name: string | null;
  cash_revenue: number;
  deposits: number;
  withdrawals: number;
  expenses: number;
  transfers_in: number;
  transfers_out: number;
  balance: number;
  contractor: ContractorLedger | null;
  entries: CashEntry[];
}

interface Transfer {
  id: string;
  from_user_id: string;
  from_name: string | null;
  to_user_id: string;
  to_name: string | null;
  amount: number;
  note: string | null;
  status: "pending" | "accepted" | "declined";
  created_at: string;
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
  // Gyors-szűrők: teljes idő (alap) / aktuális év / aktuális hónap / egyedi
  const [period, setPeriod] = useState<"all" | "year" | "month" | "custom">("all");
  const [mine, setMine] = useState<Register | null>(null);
  const [meId, setMeId] = useState<string>("");
  const [transfers, setTransfers] = useState<Transfer[]>([]);
  const [agents, setAgents] = useState<{ user_id: string | null; name: string }[]>([]);
  const [transferForm, setTransferForm] = useState<{ to: string; amount: string; note: string } | null>(null);
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
  // Beszállító-javaslatok a korábbi költésekből (szabad szavas marad)
  const [suppliers, setSuppliers] = useState<string[]>([]);
  useEffect(() => {
    api.get<string[]>("/api/stats/cash/suppliers").then(setSuppliers).catch(() => {});
  }, []);

  const params = useCallback(() => {
    const p = new URLSearchParams();
    const now = new Date();
    const iso = (d: Date) =>
      `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    if (period === "year") p.set("date_from", `${now.getFullYear()}-01-01`);
    else if (period === "month") p.set("date_from", iso(new Date(now.getFullYear(), now.getMonth(), 1)));
    else if (period === "custom") {
      if (dateFrom) p.set("date_from", dateFrom);
      if (dateTo) p.set("date_to", dateTo);
    }
    return p;
  }, [period, dateFrom, dateTo]);

  const load = useCallback(() => {
    api.get<Register>(`/api/stats/cash/me?${params()}`).then(setMine).catch(() => {});
    api.get<Transfer[]>("/api/stats/cash/transfers").then(setTransfers).catch(() => {});
    api.get<{ user_id: string | null; name: string }[]>("/api/settlements/agents")
      .then((rows) => setAgents(rows.filter((a) => a.user_id)))
      .catch(() => {});
    api.get<{ id: string }>("/api/auth/me").then((m) => setMeId(m.id)).catch(() => {});
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

  async function sendTransfer(e: React.FormEvent) {
    e.preventDefault();
    if (!transferForm) return;
    setBusy(true);
    try {
      await api.post("/api/stats/cash/transfer", {
        to_user_id: transferForm.to,
        amount: Number(transferForm.amount),
        note: transferForm.note || null,
      });
      toast(t("cash.transferSent"), "success");
      setTransferForm(null);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function decideTransfer(tr: Transfer, accept: boolean) {
    try {
      await api.post(`/api/stats/cash/transfers/${tr.id}/decide?accept=${accept}`, {});
      toast(t(accept ? "cash.transferAccepted" : "cash.transferDeclined"), "success");
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

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
        <div className="mb-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-3 lg:grid-cols-6">
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
          <div className="rounded-xl bg-indigo-50 px-3 py-2">
            <p className="text-xs text-indigo-700">{t("cash.transfersIn")}</p>
            <p className="font-semibold text-indigo-800">+{ft(r.transfers_in)}</p>
          </div>
          <div className="rounded-xl bg-slate-50 px-3 py-2">
            <p className="text-xs text-slate-500">{t("cash.transfersOut")}</p>
            <p className="font-semibold text-slate-700">−{ft(r.transfers_out)}</p>
          </div>
        </div>
        {r.contractor && (
          <div className="mb-3 rounded-2xl border border-orange-200 bg-orange-50 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-semibold text-orange-900">🔧 {t("cash.contractorTitle")}</p>
              <span className={`rounded-lg px-2.5 py-0.5 font-bold ${r.contractor.balance > 0 ? "bg-emerald-100 text-emerald-800" : r.contractor.balance < 0 ? "bg-rose-100 text-rose-800" : "bg-slate-100 text-slate-600"}`}>
                {ft(r.contractor.balance)}
              </span>
            </div>
            <p className="mb-2 text-xs text-orange-800">{t("cash.contractorHint")}</p>
            <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
              <div className="rounded-xl bg-white/70 px-3 py-2">
                <p className="text-xs text-slate-500">{t("cash.contractorFees", { n: r.contractor.worksheets })}</p>
                <p className="font-semibold">{ft(r.contractor.fee_total)}</p>
              </div>
              <div className="rounded-xl bg-white/70 px-3 py-2">
                <p className="text-xs text-slate-500">{t("svcHo.costsCol")}</p>
                <p className="font-semibold text-orange-800">+{ft(r.contractor.costs)}</p>
              </div>
              <div className="rounded-xl bg-white/70 px-3 py-2">
                <p className="text-xs text-slate-500">{t("svcHo.payoutsCol")}</p>
                <p className="font-semibold text-emerald-800">−{ft(r.contractor.payouts)}</p>
              </div>
              <div className="rounded-xl bg-white/70 px-3 py-2">
                <p className="text-xs text-slate-500">{t("svcHo.balance")}</p>
                <p className="font-semibold">{ft(r.contractor.balance)}</p>
              </div>
            </div>
          </div>
        )}
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
        <div className="flex gap-1.5">
          {([["all", t("cash.periodAll")], ["year", t("stats.year")], ["month", t("stats.month")], ["custom", t("stats.custom")]] as const).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setPeriod(key)}
              className={`rounded-full px-3 py-1.5 text-xs font-medium transition ${
                period === key ? "bg-indigo-600 text-white" : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        {period === "custom" && (
          <>
            <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <span className="text-slate-400">–</span>
            <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          </>
        )}
        <button
          onClick={() => setTransferForm({ to: "", amount: "", note: "" })}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
        >
          🤝 {t("cash.transferBtn")}
        </button>
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
        {/* Rám váró pénz-átadások: elfogadásig függőben */}
        {transfers.filter((tr) => tr.status === "pending" && tr.to_user_id === meId).map((tr) => (
          <div key={tr.id} className="flex flex-wrap items-center gap-3 rounded-2xl border border-indigo-300 bg-indigo-50 px-4 py-3">
            <span className="text-sm text-indigo-900">
              🤝 <b>{tr.from_name ?? "?"}</b> {t("cash.transferIncoming", { amount: Math.round(tr.amount).toLocaleString("hu-HU") })}
              {tr.note && <span className="text-indigo-700"> — {tr.note}</span>}
            </span>
            <button onClick={() => void decideTransfer(tr, true)} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700">
              ✔ {t("cash.transferAccept")}
            </button>
            <button onClick={() => void decideTransfer(tr, false)} className="rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-rose-700">
              ✕ {t("cash.transferDecline")}
            </button>
          </div>
        ))}
        {transfers.filter((tr) => tr.status === "pending" && tr.from_user_id === meId).map((tr) => (
          <p key={tr.id} className="rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm text-slate-500">
            ⏳ {t("cash.transferPendingOut", { name: tr.to_name ?? "?", amount: Math.round(tr.amount).toLocaleString("hu-HU") })}
          </p>
        ))}
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

      {transferForm && (
        <div onMouseDown={(e) => { if (e.target === e.currentTarget) setTransferForm(null); }} className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={sendTransfer} className="w-full max-w-sm space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">🤝 {t("cash.transferTitle")}</h2>
            <p className="text-xs text-slate-500">{t("cash.transferHint")}</p>
            <label className="block text-sm">
              {t("cash.transferTo")} *
              <select
                required
                value={transferForm.to}
                onChange={(e) => setTransferForm({ ...transferForm, to: e.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2"
              >
                <option value="">{t("tasks.choose")}</option>
                {agents.filter((a) => a.user_id && a.user_id !== meId).map((a) => (
                  <option key={a.user_id} value={a.user_id!}>{a.name}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              {t("cash.amount")} (Ft) *
              <input required type="number" min={1} value={transferForm.amount} onChange={(e) => setTransferForm({ ...transferForm, amount: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              {t("cash.note")}
              <input value={transferForm.note} onChange={(e) => setTransferForm({ ...transferForm, note: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setTransferForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
                {busy ? t("common.saving") : t("cash.transferSend")}
              </button>
            </div>
          </form>
        </div>
      )}

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
                  <input list="cash-suppliers" value={form.supplier} onChange={(e) => setForm({ ...form, supplier: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
                  <datalist id="cash-suppliers">
                    {suppliers.map((name) => (
                      <option key={name} value={name} />
                    ))}
                  </datalist>
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
