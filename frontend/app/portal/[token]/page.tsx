"use client";

/** Partner-portál (nyilvános, token-alapú): a partner belépés nélkül látja a
 *  saját külső raktár-készletét, elszámolásait és kintlévőségeit. */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface PortalData {
  partner_name: string;
  partner_code: string | null;
  stock: { product_name: string; unit: string; quantity: number; portions_available: number }[];
  settlements: {
    created_at: string;
    payment_method: string;
    total_gross: number;
    invoiced: boolean;
    payment_status: string;
    due_date: string | null;
  }[];
  outstanding_total: number;
}

export default function PortalPage() {
  const { token } = useParams<{ token: string }>();
  const { t, lang } = useT();
  const [data, setData] = useState<PortalData | null>(null);
  const [notFound, setNotFound] = useState(false);

  const fmt = (dt: string) =>
    new Date(dt).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "short", timeStyle: "short" });
  const ft = (n: number) => `${n.toLocaleString(lang === "hu" ? "hu-HU" : "en-GB")} Ft`;

  useEffect(() => {
    if (!token) return;
    api.get<PortalData>(`/api/portal/${token}`).then(setData).catch(() => setNotFound(true));
  }, [token]);

  if (notFound) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-50 p-6">
        <p className="text-slate-500">{t("portal.notFound")}</p>
      </main>
    );
  }
  if (!data) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-50 p-6">
        <p className="text-slate-400">{t("common.loading")}</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-50 px-4 py-8">
      <div className="mx-auto max-w-3xl space-y-6">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">{data.partner_name}</h1>
            {data.partner_code && <p className="text-sm text-slate-500">{data.partner_code}</p>}
          </div>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/logo.svg" alt="iwfm" className="h-9 w-auto" />
        </header>

        {data.outstanding_total > 0 && (
          <div className="rounded-2xl border border-red-200 bg-red-50 p-4">
            <p className="text-sm font-medium text-red-800">
              {t("portal.outstanding", { total: data.outstanding_total.toLocaleString("hu-HU") })}
            </p>
          </div>
        )}

        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 font-semibold">{t("portal.stockTitle")}</h2>
          {data.stock.length === 0 ? (
            <p className="text-sm text-slate-400">{t("portal.noStock")}</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
                  <th className="py-2">{t("cons.name")}</th>
                  <th className="py-2">{t("cons.bookQty")}</th>
                  <th className="py-2">{t("cons.portionsAvail")}</th>
                </tr>
              </thead>
              <tbody>
                {data.stock.map((s) => (
                  <tr key={s.product_name} className="border-b border-slate-100 last:border-0">
                    <td className="py-2 font-medium">{s.product_name}</td>
                    <td className="py-2">{s.quantity} {s.unit}</td>
                    <td className="py-2 text-slate-500">{s.portions_available}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 font-semibold">{t("portal.settlementsTitle")}</h2>
          {data.settlements.length === 0 ? (
            <p className="text-sm text-slate-400">{t("cons.noSettlements")}</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
                  <th className="py-2">{t("cons.date")}</th>
                  <th className="py-2">{t("cons.payment")}</th>
                  <th className="py-2">{t("cons.total")}</th>
                  <th className="py-2">{t("portal.status")}</th>
                </tr>
              </thead>
              <tbody>
                {data.settlements.map((s, i) => (
                  <tr key={i} className="border-b border-slate-100 last:border-0">
                    <td className="py-2 whitespace-nowrap">{fmt(s.created_at)}</td>
                    <td className="py-2">{t(`cons.payments.${s.payment_method}`)}</td>
                    <td className="py-2 font-medium">{ft(s.total_gross)}</td>
                    <td className="py-2">
                      {!s.invoiced ? (
                        <span className="rounded bg-slate-100 px-2 py-0.5 text-xs">{t("cons.notInvoiced")}</span>
                      ) : s.payment_status === "paid" ? (
                        <span className="rounded bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800">{t("portal.paid")}</span>
                      ) : (
                        <span className="rounded bg-red-100 px-2 py-0.5 text-xs text-red-700">
                          {t("portal.unpaid")}{s.due_date ? ` · ${s.due_date}` : ""}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <footer className="pb-4 text-center text-xs text-slate-400">
          {t("portal.footer")}
        </footer>
      </div>
    </main>
  );
}
