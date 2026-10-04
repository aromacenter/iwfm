"use client";

/** Szerelő-átadás: a külsős szerelőnél lévő kész gépek tömeges átvétele —
 * ki kinek mit ad át, mennyi javítási díj jár a szerelőnek. Átvételkor az
 * ügyfél (best-effort) emailt kap, hogy a készülék átvehető. A szerelő
 * készpénzes költései/levonásai a Kassza modulban könyvelhetők. */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Row {
  task_id: string;
  serial: string;
  title: string;
  client_name: string | null;
  employee_id: string;
  employee_name: string | null;
  quote_status: string;
  completed: boolean;
  fee_total: number;
  quote_email: string | null;
}

interface FeeRow {
  employee_id: string;
  employee_name: string | null;
  fee_total: number;
  count: number;
  serials: string[];
}

export default function SzereloAtadasPage() {
  const { t, lang } = useT();
  const { toast, confirm } = useUI();
  const ft = (n: number) => `${Math.round(n).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB")} Ft`;

  const [rows, setRows] = useState<Row[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [notify, setNotify] = useState(true);
  const [busy, setBusy] = useState(false);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [fees, setFees] = useState<FeeRow[]>([]);

  const load = useCallback(() => {
    api.get<Row[]>("/api/tasks/service-handover/list").then(setRows).catch(() => {});
    const p = new URLSearchParams();
    if (dateFrom) p.set("date_from", dateFrom);
    if (dateTo) p.set("date_to", dateTo);
    api.get<FeeRow[]>(`/api/tasks/service-handover/fees?${p}`).then(setFees).catch(() => {});
  }, [dateFrom, dateTo]);
  useEffect(load, [load]);

  // Szerelőnként csoportosítva
  const byEmp = rows.reduce<Record<string, Row[]>>((acc, r) => {
    (acc[r.employee_id] = acc[r.employee_id] ?? []).push(r);
    return acc;
  }, {});

  const selectedFee = rows
    .filter((r) => selected.has(r.task_id))
    .reduce((a, r) => a + r.fee_total, 0);

  async function pickup() {
    if (selected.size === 0) return;
    if (!(await confirm(t("svcHo.confirm", {
      count: selected.size,
      fee: Math.round(selectedFee).toLocaleString("hu-HU"),
    })))) return;
    setBusy(true);
    try {
      const res = await api.post<{ picked: number; fee_total: number }>(
        "/api/tasks/service-handover/pickup",
        { task_ids: [...selected], notify },
      );
      toast(t("svcHo.done", {
        count: res.picked,
        fee: Math.round(res.fee_total).toLocaleString("hu-HU"),
      }), "success");
      setSelected(new Set());
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">🔧🤝 {t("svcHo.title")}</h1>
        <label className="flex items-center gap-1.5 text-sm text-slate-600">
          <input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} className="h-4 w-4" />
          ✉ {t("svcHo.notify")}
        </label>
        {selected.size > 0 && (
          <button
            onClick={pickup}
            disabled={busy}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {busy ? t("common.saving") : t("svcHo.pickupBtn", { count: selected.size, fee: Math.round(selectedFee).toLocaleString("hu-HU") })}
          </button>
        )}
      </div>

      {Object.keys(byEmp).length === 0 && (
        <p className="rounded-2xl border border-slate-200 bg-white px-4 py-10 text-center text-slate-400">
          {t("svcHo.empty")}
        </p>
      )}

      <div className="space-y-4">
        {Object.entries(byEmp).map(([empId, list]) => (
          <div key={empId} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-semibold">🔧 {list[0].employee_name ?? "?"}</h2>
              <span className="rounded-lg bg-orange-50 px-3 py-1 text-sm font-semibold text-orange-700">
                {t("svcHo.feeDue")}: {ft(list.reduce((a, r) => a + r.fee_total, 0))}
              </span>
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-slate-500">
                  <th className="w-8 py-1.5">
                    <input
                      type="checkbox"
                      checked={list.every((r) => selected.has(r.task_id))}
                      onChange={(e) =>
                        setSelected((s) => {
                          const next = new Set(s);
                          list.forEach((r) => (e.target.checked ? next.add(r.task_id) : next.delete(r.task_id)));
                          return next;
                        })
                      }
                      className="h-4 w-4"
                    />
                  </th>
                  <th className="py-1.5 pr-2">{t("svcHo.worksheet")}</th>
                  <th className="py-1.5 pr-2">{t("svcHo.client")}</th>
                  <th className="py-1.5 pr-2">{t("svcHo.status")}</th>
                  <th className="py-1.5 text-right">{t("svcHo.fee")}</th>
                </tr>
              </thead>
              <tbody>
                {list.map((r) => (
                  <tr key={r.task_id} className="border-t border-slate-100">
                    <td className="py-1.5">
                      <input
                        type="checkbox"
                        checked={selected.has(r.task_id)}
                        onChange={() =>
                          setSelected((s) => {
                            const next = new Set(s);
                            if (next.has(r.task_id)) next.delete(r.task_id);
                            else next.add(r.task_id);
                            return next;
                          })
                        }
                        className="h-4 w-4"
                      />
                    </td>
                    <td className="py-1.5 pr-2">
                      <span className="font-mono text-xs font-semibold">{r.serial}</span>{" "}
                      <span className="text-slate-600">{r.title}</span>
                    </td>
                    <td className="py-1.5 pr-2 text-slate-600">{r.client_name ?? "—"}</td>
                    <td className="py-1.5 pr-2">
                      {r.completed ? (
                        <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-xs font-medium text-emerald-800">✔ {t("svcHo.ready")}</span>
                      ) : (
                        <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800">⏳ {t("svcHo.inProgress")}</span>
                      )}
                      {!r.quote_email && (
                        <span title={t("svcHo.noEmailHint")} className="ml-1 text-xs text-slate-400">✉?</span>
                      )}
                    </td>
                    <td className="py-1.5 text-right font-medium tabular-nums">{ft(r.fee_total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>

      {/* Elhozott gépek díj-összesítése időszakra — ennyi jár a szerelőnek */}
      <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-2 flex flex-wrap items-center gap-3">
          <h2 className="font-semibold">💵 {t("svcHo.feesTitle")}</h2>
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <span className="text-slate-400">–</span>
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <Link href="/kassza" className="text-sm text-indigo-600 hover:underline">
            💰 {t("svcHo.cashLink")}
          </Link>
        </div>
        <p className="mb-2 text-xs text-slate-400">{t("svcHo.feesHint")}</p>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-slate-500">
              <th className="py-1.5 pr-2">{t("svcHo.technician")}</th>
              <th className="py-1.5 pr-2 text-right">{t("svcHo.feeDue")}</th>
              <th className="py-1.5 pr-2 text-right">{t("stats.count")}</th>
              <th className="py-1.5">{t("svcHo.worksheets")}</th>
            </tr>
          </thead>
          <tbody>
            {fees.map((f) => (
              <tr key={f.employee_id} className="border-t border-slate-100">
                <td className="py-1.5 pr-2 font-medium">{f.employee_name ?? "?"}</td>
                <td className="py-1.5 pr-2 text-right font-semibold tabular-nums">{ft(f.fee_total)}</td>
                <td className="py-1.5 pr-2 text-right tabular-nums text-slate-400">{f.count}</td>
                <td className="py-1.5 font-mono text-xs text-slate-500">{f.serials.join(", ")}</td>
              </tr>
            ))}
            {fees.length === 0 && (
              <tr><td colSpan={4} className="py-6 text-center text-slate-400">{t("stats.empty")}</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </AppShell>
  );
}
