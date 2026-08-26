"use client";

/** Átadás: a megjavított, a szerelőtől már elhozott gépek átadása az
 *  ügyfélnek. Gépen: lista a várakozókról; a munkalap QR-kódja (?task=…)
 *  ugyanide hoz, azonnal a kiválasztott géppel. Fizetés (készpénz/kártya),
 *  kedvezmény-opció (= nem készül számla), Billingó-számla, majd a gép
 *  átadott státuszt kap és a munkalap lezárul. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface HandoverItem {
  name: string;
  amount_net: number;
}

interface Handover {
  task_id: string;
  serial: string;
  title: string;
  machine: string | null;
  client_name: string | null;
  client_location: string | null;
  quote_email: string | null;
  picked_up_at: string | null;
  handed_over_at: string | null;
  handover_discount: boolean;
  handover_payment_method: string | null;
  handover_document_id: string | null;
  items: HandoverItem[];
  total_net: number;
  total_gross: number;
}

const ft = (n: number) => `${Math.round(n).toLocaleString("hu-HU")} Ft`;

export default function AtadasPage() {
  const { t } = useT();
  const { toast, confirm } = useUI();

  const [rows, setRows] = useState<Handover[]>([]);
  const [selected, setSelected] = useState<Handover | null>(null);
  const [payment, setPayment] = useState<"cash" | "card">("cash");
  const [discount, setDiscount] = useState(false);
  const [busy, setBusy] = useState(false);
  const [doneInfo, setDoneInfo] = useState<{ serial: string; discount: boolean; document_id: string | null } | null>(null);

  const load = useCallback(() => {
    api.get<Handover[]>("/api/tasks/handovers/list").then(setRows).catch(() => {});
  }, []);
  useEffect(load, [load]);

  // QR-ről érkezés: ?task=<id> → azonnal a részletek
  useEffect(() => {
    const taskId = new URLSearchParams(window.location.search).get("task");
    if (!taskId) return;
    api.get<Handover>(`/api/tasks/${taskId}/handover`)
      .then((h) => {
        setSelected(h);
        setDiscount(false);
        setPayment("cash");
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function openRow(h: Handover) {
    setSelected(h);
    setDiscount(false);
    setPayment("cash");
    setDoneInfo(null);
  }

  async function doHandover() {
    if (!selected) return;
    const text = discount
      ? t("handover.confirmDiscount", { serial: selected.serial })
      : t("handover.confirmPay", {
          serial: selected.serial,
          total: ft(selected.total_gross),
          method: payment === "card" ? t("handover.card") : t("handover.cash"),
        });
    if (!(await confirm(text))) return;
    setBusy(true);
    try {
      const res = await api.post<{ ok: boolean; document_id: string | null }>(
        `/api/tasks/${selected.task_id}/handover`,
        { payment_method: payment, discount }
      );
      setDoneInfo({ serial: selected.serial, discount, document_id: res.document_id });
      setSelected(null);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell>
      <div className="mb-4">
        <h1 className="text-2xl font-semibold">🤝 {t("handover.title")}</h1>
        <p className="text-sm text-slate-500">{t("handover.subtitle")}</p>
      </div>

      {doneInfo && (
        <div className="mb-4 rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900">
          ✔{" "}
          {doneInfo.discount
            ? t("handover.doneDiscount", { serial: doneInfo.serial })
            : t("handover.doneInvoiced", { serial: doneInfo.serial })}
        </div>
      )}

      {!selected && (
        <div className="space-y-3">
          {rows.length === 0 && (
            <p className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500">
              {t("handover.empty")}
            </p>
          )}
          {rows.map((h) => (
            <button
              key={h.task_id}
              onClick={() => openRow(h)}
              className="block w-full rounded-2xl border border-slate-200 bg-white p-4 text-left shadow-sm transition hover:border-indigo-300"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="font-semibold">
                    {h.serial}
                    <span className="ml-2 font-normal text-slate-600">{h.machine}</span>
                  </p>
                  <p className="mt-0.5 text-xs text-slate-500">
                    {h.client_name ?? "—"}
                    {h.picked_up_at && ` · ${t("handover.pickedUp")}: ${h.picked_up_at.slice(0, 10)}`}
                  </p>
                </div>
                <span className="text-right text-sm font-semibold">
                  {ft(h.total_gross)}
                  <span className="block text-xs font-normal text-slate-400">
                    {t("handover.grossLabel")}
                  </span>
                </span>
              </div>
            </button>
          ))}
        </div>
      )}

      {selected && (
        <div className="max-w-lg space-y-3">
          <button onClick={() => setSelected(null)} className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
            ← {t("handover.back")}
          </button>
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <p className="text-xs uppercase tracking-wide text-slate-400">{selected.serial}</p>
            <h2 className="mt-1 text-lg font-semibold">☕ {selected.machine ?? selected.title}</h2>
            <p className="mt-1 text-sm text-slate-600">
              {selected.client_name ?? "—"}
              {selected.client_location ? ` · ${selected.client_location}` : ""}
              {selected.quote_email ? ` · ${selected.quote_email}` : ""}
            </p>

            <div className="mt-4 space-y-1 border-t border-slate-100 pt-3">
              {selected.items.map((it, i) => (
                <div key={i} className="flex justify-between text-sm">
                  <span>{it.name}</span>
                  <span>{ft(it.amount_net)}</span>
                </div>
              ))}
              <div className="mt-2 flex justify-between border-t border-slate-200 pt-2 text-sm font-semibold">
                <span>{t("handover.totalNet")}</span>
                <span>{ft(selected.total_net)}</span>
              </div>
              <div className="flex justify-between text-base font-bold">
                <span>{t("handover.totalGross")}</span>
                <span>{ft(selected.total_gross)}</span>
              </div>
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={discount}
                onChange={(e) => setDiscount(e.target.checked)}
                className="h-4 w-4"
              />
              <span className="font-medium">{t("handover.discount")}</span>
            </label>

            {!discount && (
              <div className="mt-3 flex gap-2">
                {(["cash", "card"] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => setPayment(m)}
                    className={`flex-1 rounded-xl border px-3 py-2.5 text-sm font-medium transition ${
                      payment === m
                        ? "border-indigo-500 bg-indigo-50 text-indigo-800 ring-1 ring-indigo-500"
                        : "border-slate-200 hover:border-slate-300"
                    }`}
                  >
                    {m === "cash" ? `💵 ${t("handover.cash")}` : `💳 ${t("handover.card")}`}
                  </button>
                ))}
              </div>
            )}

            <button
              onClick={doHandover}
              disabled={busy}
              className="mt-4 w-full rounded-xl bg-indigo-600 px-4 py-3 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              🤝 {discount ? t("handover.submitDiscount") : t("handover.submitPay")}
            </button>
            <p className="mt-2 text-center text-xs text-slate-400">{t("handover.submitHint")}</p>
          </div>
        </div>
      )}
    </AppShell>
  );
}
