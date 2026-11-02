"use client";

/** Partner-portál (nyilvános, token-alapú): a partner belépés nélkül látja a
 *  saját külső raktár-készletét, elszámolásait és kintlévőségeit — és a 129.
 *  kör óta önkiszolgáló is: gépek + számlálók, a következő elszámolás várható
 *  ideje és becsült összege, termékrendelés és szervizkérés közvetlenül innen. */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface PortalAsset {
  name: string;
  barcode: string;
  serial_number: string | null;
  counter: number | null;
  counters: number[] | null;
  counter_names: string[] | null;
  support_path: string | null;
}

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
  assets: PortalAsset[];
  next_settlement: {
    kind: "fixed" | "consumption";
    due_date: string | null;
    estimated_gross: number | null;
    cycle: string;
  };
  products: { id: string; name: string; unit: string }[];
  orders: { order_no: string; status: string; created_at: string; items: { name: string; quantity: number; unit: string }[] }[];
  tickets: { ticket_no: string; title: string; status: string; created_at: string }[];
}

interface OrderRow {
  product_id: string;
  quantity: string;
}

const inputCls =
  "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none";

export default function PortalPage() {
  const { token } = useParams<{ token: string }>();
  const { t, lang } = useT();
  const [data, setData] = useState<PortalData | null>(null);
  const [notFound, setNotFound] = useState(false);

  // rendelés űrlap
  const [orderRows, setOrderRows] = useState<OrderRow[]>([{ product_id: "", quantity: "1" }]);
  const [orderNote, setOrderNote] = useState("");
  const [orderName, setOrderName] = useState("");
  const [orderPhone, setOrderPhone] = useState("");
  const [orderBusy, setOrderBusy] = useState(false);
  const [orderMsg, setOrderMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // szervizkérés űrlap
  const [tkAsset, setTkAsset] = useState("");
  const [tkDesc, setTkDesc] = useState("");
  const [tkPhone, setTkPhone] = useState("");
  const [tkEmail, setTkEmail] = useState("");
  const [tkBusy, setTkBusy] = useState(false);
  const [tkMsg, setTkMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const locale = lang === "hu" ? "hu-HU" : "en-GB";
  const fmt = (dt: string) => new Date(dt).toLocaleString(locale, { dateStyle: "short", timeStyle: "short" });
  const fd = (d: string) => new Date(d).toLocaleDateString(locale, { dateStyle: "long" });
  const ft = (n: number) => `${n.toLocaleString(locale)} Ft`;

  const load = () =>
    api.get<PortalData>(`/api/portal/${token}`).then(setData).catch(() => setNotFound(true));

  useEffect(() => {
    if (!token) return;
    api.get<PortalData>(`/api/portal/${token}`).then(setData).catch(() => setNotFound(true));
  }, [token]);

  const statusLabel = (kind: "order" | "ticket", status: string) => {
    const key = `portal.${kind}Status.${status}`;
    const label = t(key);
    return label === key ? status : label;
  };

  const submitOrder = async () => {
    if (!data) return;
    const items = orderRows
      .filter((r) => r.product_id && Number(r.quantity) > 0)
      .map((r) => ({ product_id: r.product_id, quantity: Number(r.quantity) }));
    if (items.length === 0) {
      setOrderMsg({ ok: false, text: t("portal.orderEmpty") });
      return;
    }
    setOrderBusy(true);
    setOrderMsg(null);
    try {
      const res = await api.post<{ order_no: string }>(`/api/portal/${token}/order`, {
        items,
        note: orderNote || null,
        contact_name: orderName || null,
        contact_phone: orderPhone || null,
      });
      setOrderMsg({ ok: true, text: t("portal.orderSent", { no: res.order_no }) });
      setOrderRows([{ product_id: "", quantity: "1" }]);
      setOrderNote("");
      await load();
    } catch (err) {
      setOrderMsg({ ok: false, text: errorMessage(err) });
    } finally {
      setOrderBusy(false);
    }
  };

  const submitTicket = async () => {
    if (tkDesc.trim().length < 5) {
      setTkMsg({ ok: false, text: t("portal.ticketTooShort") });
      return;
    }
    setTkBusy(true);
    setTkMsg(null);
    try {
      const res = await api.post<{ ticket_no: string }>(`/api/portal/${token}/ticket`, {
        description: tkDesc.trim(),
        asset_barcode: tkAsset || null,
        contact_phone: tkPhone || null,
        contact_email: tkEmail || null,
      });
      setTkMsg({ ok: true, text: t("portal.ticketSent", { no: res.ticket_no }) });
      setTkDesc("");
      await load();
    } catch (err) {
      setTkMsg({ ok: false, text: errorMessage(err) });
    } finally {
      setTkBusy(false);
    }
  };

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

  const ns = data.next_settlement;

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

        {/* Következő elszámolás */}
        <section className="rounded-2xl border border-sky-200 bg-sky-50 p-5 shadow-sm">
          <h2 className="mb-1 font-semibold text-sky-900">{t("portal.nextTitle")}</h2>
          {ns.due_date ? (
            <p className="text-sm text-sky-900">
              <span className="font-medium">{fd(ns.due_date)}</span>
              {ns.estimated_gross != null && (
                <>
                  {" · "}
                  {t("portal.nextEstimate", { total: ns.estimated_gross.toLocaleString(locale) })}
                </>
              )}
            </p>
          ) : (
            <p className="text-sm text-sky-800">{t("portal.nextUnknown")}</p>
          )}
          <p className="mt-1 text-xs text-sky-700">
            {ns.kind === "fixed" ? t("portal.nextFixedHint") : t("portal.nextConsumptionHint")}
          </p>
        </section>

        {/* Gépek + számlálók */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 font-semibold">{t("portal.assetsTitle")}</h2>
          {data.assets.length === 0 ? (
            <p className="text-sm text-slate-400">{t("portal.noAssets")}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
                    <th className="py-2">{t("portal.assetName")}</th>
                    <th className="py-2">{t("portal.assetCounter")}</th>
                    <th className="py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {data.assets.map((a) => (
                    <tr key={a.barcode} className="border-b border-slate-100 last:border-0">
                      <td className="py-2">
                        <div className="font-medium">{a.name}</div>
                        <div className="text-xs text-slate-500">
                          {a.barcode}
                          {a.serial_number ? ` · ${a.serial_number}` : ""}
                        </div>
                      </td>
                      <td className="py-2">
                        {a.counter != null ? a.counter.toLocaleString(locale) : "—"}
                        {a.counters && a.counters.length > 1 && (
                          <div className="text-xs text-slate-500">
                            {a.counters
                              .map((c, i) => `${a.counter_names?.[i] || `#${i + 1}`}: ${c.toLocaleString(locale)}`)
                              .join(" · ")}
                          </div>
                        )}
                      </td>
                      <td className="py-2 text-right">
                        {a.support_path && (
                          <a
                            href={a.support_path}
                            className="rounded-lg border border-slate-300 px-2 py-1 text-xs text-slate-700 hover:bg-slate-50"
                          >
                            {t("portal.reportCounter")}
                          </a>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 font-semibold">{t("portal.stockTitle")}</h2>
          {data.stock.length === 0 ? (
            <p className="text-sm text-slate-400">{t("portal.noStock")}</p>
          ) : (
            <div className="overflow-x-auto">
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
            </div>
          )}
        </section>

        {/* Rendelés */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-1 font-semibold">{t("portal.orderTitle")}</h2>
          <p className="mb-3 text-xs text-slate-500">{t("portal.orderHint")}</p>
          <div className="space-y-2">
            {orderRows.map((row, i) => (
              <div key={i} className="flex gap-2">
                <select
                  className={inputCls}
                  value={row.product_id}
                  onChange={(e) =>
                    setOrderRows((rows) => rows.map((r, j) => (j === i ? { ...r, product_id: e.target.value } : r)))
                  }
                >
                  <option value="">{t("portal.pickProduct")}</option>
                  {data.products.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} ({p.unit})
                    </option>
                  ))}
                </select>
                <input
                  type="number"
                  min={0.1}
                  step={0.1}
                  className={`${inputCls} w-24`}
                  value={row.quantity}
                  onChange={(e) =>
                    setOrderRows((rows) => rows.map((r, j) => (j === i ? { ...r, quantity: e.target.value } : r)))
                  }
                />
                {orderRows.length > 1 && (
                  <button
                    type="button"
                    className="px-2 text-slate-400 hover:text-red-600"
                    onClick={() => setOrderRows((rows) => rows.filter((_, j) => j !== i))}
                    aria-label={t("common.delete")}
                  >
                    ×
                  </button>
                )}
              </div>
            ))}
            {orderRows.length < 10 && (
              <button
                type="button"
                className="text-xs text-sky-700 hover:underline"
                onClick={() => setOrderRows((rows) => [...rows, { product_id: "", quantity: "1" }])}
              >
                + {t("portal.addRow")}
              </button>
            )}
            <textarea
              className={inputCls}
              rows={2}
              placeholder={t("portal.orderNote")}
              value={orderNote}
              onChange={(e) => setOrderNote(e.target.value)}
            />
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <input
                className={inputCls}
                placeholder={t("portal.contactName")}
                value={orderName}
                onChange={(e) => setOrderName(e.target.value)}
              />
              <input
                className={inputCls}
                placeholder={t("portal.contactPhone")}
                value={orderPhone}
                onChange={(e) => setOrderPhone(e.target.value)}
              />
            </div>
            <button
              type="button"
              disabled={orderBusy}
              onClick={submitOrder}
              className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
            >
              {orderBusy ? t("common.loading") : t("portal.orderSubmit")}
            </button>
            {orderMsg && (
              <p className={`text-sm ${orderMsg.ok ? "text-emerald-700" : "text-red-700"}`}>{orderMsg.text}</p>
            )}
          </div>
        </section>

        {/* Szervizkérés */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-1 font-semibold">{t("portal.ticketTitle")}</h2>
          <p className="mb-3 text-xs text-slate-500">{t("portal.ticketHint")}</p>
          <div className="space-y-2">
            {data.assets.length > 0 && (
              <select className={inputCls} value={tkAsset} onChange={(e) => setTkAsset(e.target.value)}>
                <option value="">{t("portal.ticketAnyAsset")}</option>
                {data.assets.map((a) => (
                  <option key={a.barcode} value={a.barcode}>
                    {a.name} · {a.barcode}
                  </option>
                ))}
              </select>
            )}
            <textarea
              className={inputCls}
              rows={3}
              placeholder={t("portal.ticketDesc")}
              value={tkDesc}
              onChange={(e) => setTkDesc(e.target.value)}
            />
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <input
                className={inputCls}
                placeholder={t("portal.contactPhone")}
                value={tkPhone}
                onChange={(e) => setTkPhone(e.target.value)}
              />
              <input
                className={inputCls}
                type="email"
                placeholder={t("portal.contactEmail")}
                value={tkEmail}
                onChange={(e) => setTkEmail(e.target.value)}
              />
            </div>
            <button
              type="button"
              disabled={tkBusy}
              onClick={submitTicket}
              className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
            >
              {tkBusy ? t("common.loading") : t("portal.ticketSubmit")}
            </button>
            {tkMsg && <p className={`text-sm ${tkMsg.ok ? "text-emerald-700" : "text-red-700"}`}>{tkMsg.text}</p>}
          </div>
        </section>

        {/* Saját kérések */}
        {(data.orders.length > 0 || data.tickets.length > 0) && (
          <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="mb-3 font-semibold">{t("portal.requestsTitle")}</h2>
            <ul className="divide-y divide-slate-100 text-sm">
              {data.orders.map((o) => (
                <li key={o.order_no} className="flex items-start justify-between gap-3 py-2">
                  <div>
                    <div className="font-medium">
                      {t("portal.orderLabel")} {o.order_no}
                      <span className="ml-2 text-xs text-slate-500">{fmt(o.created_at)}</span>
                    </div>
                    <div className="text-xs text-slate-500">
                      {o.items.map((it) => `${it.name} (${it.quantity} ${it.unit})`).join(", ")}
                    </div>
                  </div>
                  <span
                    className={`rounded px-2 py-0.5 text-xs ${
                      o.status === "done" ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"
                    }`}
                  >
                    {statusLabel("order", o.status)}
                  </span>
                </li>
              ))}
              {data.tickets.map((tk) => (
                <li key={tk.ticket_no} className="flex items-start justify-between gap-3 py-2">
                  <div>
                    <div className="font-medium">
                      {t("portal.ticketLabel")} {tk.ticket_no}
                      <span className="ml-2 text-xs text-slate-500">{fmt(tk.created_at)}</span>
                    </div>
                    <div className="text-xs text-slate-500">{tk.title}</div>
                  </div>
                  <span
                    className={`rounded px-2 py-0.5 text-xs ${
                      tk.status === "done" || tk.status === "closed"
                        ? "bg-emerald-100 text-emerald-800"
                        : "bg-amber-100 text-amber-800"
                    }`}
                  >
                    {statusLabel("ticket", tk.status)}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 font-semibold">{t("portal.settlementsTitle")}</h2>
          {data.settlements.length === 0 ? (
            <p className="text-sm text-slate-400">{t("cons.noSettlements")}</p>
          ) : (
            <div className="overflow-x-auto">
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
            </div>
          )}
        </section>

        <footer className="pb-4 text-center text-xs text-slate-400">
          {t("portal.footer")}
        </footer>
      </div>
    </main>
  );
}
