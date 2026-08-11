"use client";

/** Nyilvános gép-támogatási oldal (QR-kódról nyílik, belépés nélkül).
 *  A gép + ügyfél adatai előtöltve; két út:
 *   - Azonnali segítség: AI chat a tudásbázis alapján
 *   - Szervizigény: bejelentő űrlap hibaleírással + fotókkal → szervizjegy */

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { api, ApiError, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface SupportInfo {
  asset_name: string;
  barcode: string;
  serial_number: string | null;
  location_type: string | null;
  counter: number | null;
  partner_name: string | null;
  partner_code: string | null;
  contact_name: string | null;
  contact_phone: string | null;
  products: { id: string; name: string; unit: string }[];
}

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
}

const MAX_PHOTOS = 3;

export default function TamogatasPage() {
  const { token } = useParams<{ token: string }>();
  const { t } = useT();
  const [info, setInfo] = useState<SupportInfo | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [mode, setMode] = useState<
    "choose" | "chat" | "ticket" | "done" | "counter" | "counter_done" | "order" | "order_done"
  >("choose");

  // chat állapot
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // bejelentő állapot
  const [description, setDescription] = useState("");
  const [contactName, setContactName] = useState("");
  const [contactPhone, setContactPhone] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [photos, setPhotos] = useState<{ name: string; dataUrl: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ticketNo, setTicketNo] = useState<string | null>(null);

  // számláló-bejelentés
  const [counterVal, setCounterVal] = useState("");
  const [reporterName, setReporterName] = useState("");
  const [counterResult, setCounterResult] = useState<{ old: number | null; next: number } | null>(null);

  // termékrendelés
  const [orderItems, setOrderItems] = useState<{ product_id: string; quantity: string }[]>([]);
  const [orderNote, setOrderNote] = useState("");
  const [orderNo, setOrderNo] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    api.get<SupportInfo>(`/api/support/${token}`)
      .then((data) => {
        setInfo(data);
        setContactName(data.contact_name ?? "");
        setContactPhone(data.contact_phone ?? "");
        setReporterName(data.contact_name ?? "");
        if (data.products.length > 0) {
          setOrderItems([{ product_id: data.products[0].id, quantity: "1" }]);
        }
      })
      .catch(() => setNotFound(true));
  }, [token]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, chatBusy]);

  async function sendChat(e: React.FormEvent) {
    e.preventDefault();
    const text = chatInput.trim();
    if (!text || chatBusy) return;
    const next: ChatMsg[] = [...messages, { role: "user", content: text }];
    setMessages(next);
    setChatInput("");
    setChatBusy(true);
    try {
      const res = await api.post<{ reply: string }>(`/api/support/${token}/chat`, {
        messages: next.slice(-8),
      });
      setMessages([...next, { role: "assistant", content: res.reply }]);
    } catch (err) {
      if (err instanceof ApiError && err.code === "support.ai_not_configured") {
        setMessages([...next, { role: "assistant", content: t("support.aiUnavailable") }]);
      } else {
        setMessages([...next, { role: "assistant", content: errorMessage(err) }]);
      }
    } finally {
      setChatBusy(false);
    }
  }

  function addPhotos(files: FileList | null) {
    if (!files) return;
    setError(null);
    for (const file of Array.from(files)) {
      if (photos.length >= MAX_PHOTOS) break;
      if (!/^image\/(png|jpeg|webp)$/.test(file.type)) continue;
      if (file.size > 4 * 1024 * 1024) {
        setError(t("support.photoTooLarge", { name: file.name }));
        continue;
      }
      const reader = new FileReader();
      reader.onload = () =>
        setPhotos((prev) =>
          prev.length < MAX_PHOTOS
            ? [...prev, { name: file.name, dataUrl: reader.result as string }]
            : prev,
        );
      reader.readAsDataURL(file);
    }
  }

  async function submitCounter(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api.post<{ old_counter: number | null; new_counter: number }>(
        `/api/support/${token}/counter`,
        { counter: Number(counterVal), reporter_name: reporterName || null },
      );
      setCounterResult({ old: res.old_counter, next: res.new_counter });
      setMode("counter_done");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function submitOrder(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api.post<{ order_no: string }>(`/api/support/${token}/order`, {
        items: orderItems
          .filter((it) => it.product_id && Number(it.quantity) > 0)
          .map((it) => ({ product_id: it.product_id, quantity: Number(it.quantity) })),
        note: orderNote || null,
        contact_name: contactName || null,
        contact_phone: contactPhone || null,
      });
      setOrderNo(res.order_no);
      setMode("order_done");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function submitTicket(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api.post<{ ticket_no: string }>(`/api/support/${token}/ticket`, {
        description,
        contact_name: contactName || null,
        contact_phone: contactPhone || null,
        contact_email: contactEmail.trim() || null,
        photos: photos.map((p) => p.dataUrl),
      });
      setTicketNo(res.ticket_no);
      setMode("done");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (notFound) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-50 p-6">
        <p className="text-slate-500">{t("support.notFound")}</p>
      </main>
    );
  }
  if (!info) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-50 p-6">
        <p className="text-slate-400">{t("common.loading")}</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-50 px-4 py-6">
      <div className="mx-auto max-w-xl space-y-4">
        <header className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h1 className="text-lg font-bold">{info.asset_name}</h1>
              <p className="text-sm text-slate-500">
                {t("support.machineCode")}: <span className="font-mono">{info.barcode}</span>
                {info.serial_number && <> · {t("support.serial")}: {info.serial_number}</>}
              </p>
              {info.partner_name && (
                <p className="text-sm text-slate-500">
                  {info.partner_name}
                  {info.partner_code && <span className="ml-1 text-xs text-slate-400">{info.partner_code}</span>}
                </p>
              )}
            </div>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/logo.svg" alt="iwfm" className="h-8 w-auto" />
          </div>
        </header>

        {mode === "choose" && (
          <div className="grid gap-3">
            <button
              onClick={() => setMode("chat")}
              className="rounded-2xl border border-indigo-200 bg-white p-5 text-left shadow-sm transition hover:border-indigo-400"
            >
              <p className="text-lg font-semibold text-indigo-700">💬 {t("support.chatOption")}</p>
              <p className="mt-1 text-sm text-slate-500">{t("support.chatOptionHint")}</p>
            </button>
            <button
              onClick={() => setMode("ticket")}
              className="rounded-2xl border border-amber-200 bg-white p-5 text-left shadow-sm transition hover:border-amber-400"
            >
              <p className="text-lg font-semibold text-amber-700">🔧 {t("support.ticketOption")}</p>
              <p className="mt-1 text-sm text-slate-500">{t("support.ticketOptionHint")}</p>
            </button>
            <button
              onClick={() => { setCounterVal(""); setError(null); setMode("counter"); }}
              className="rounded-2xl border border-sky-200 bg-white p-5 text-left shadow-sm transition hover:border-sky-400"
            >
              <p className="text-lg font-semibold text-sky-700">🔢 {t("support.counterOption")}</p>
              <p className="mt-1 text-sm text-slate-500">
                {t("support.counterOptionHint")}
                {info.counter != null && <> {t("support.counterCurrent", { counter: info.counter })}</>}
              </p>
            </button>
            {info.products.length > 0 && (
              <button
                onClick={() => { setError(null); setMode("order"); }}
                className="rounded-2xl border border-emerald-200 bg-white p-5 text-left shadow-sm transition hover:border-emerald-400"
              >
                <p className="text-lg font-semibold text-emerald-700">🛒 {t("support.orderOption")}</p>
                <p className="mt-1 text-sm text-slate-500">{t("support.orderOptionHint")}</p>
              </button>
            )}
          </div>
        )}

        {mode === "counter" && (
          <form onSubmit={submitCounter} className="space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between">
              <p className="font-semibold">{t("support.counterTitle")}</p>
              <button type="button" onClick={() => setMode("choose")} className="text-sm text-slate-400 hover:text-slate-600">
                ← {t("support.back")}
              </button>
            </div>
            {info.counter != null && (
              <p className="text-sm text-slate-500">{t("support.counterCurrent", { counter: info.counter })}</p>
            )}
            <label className="block text-sm">
              {t("support.counterNew")} *
              <input
                required
                type="number"
                min={0}
                inputMode="numeric"
                value={counterVal}
                onChange={(e) => setCounterVal(e.target.value)}
                placeholder={t("support.counterPh")}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-lg tabular-nums"
              />
            </label>
            <label className="block text-sm">
              {t("support.reporterName")}
              <input
                value={reporterName}
                onChange={(e) => setReporterName(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <button
              disabled={busy || counterVal === ""}
              className="w-full rounded-xl bg-sky-600 px-4 py-2.5 font-medium text-white hover:bg-sky-700 disabled:opacity-50"
            >
              {busy ? t("common.saving") : t("support.counterSubmit")}
            </button>
          </form>
        )}

        {mode === "counter_done" && counterResult && (
          <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-6 text-center shadow-sm">
            <p className="text-3xl">✅</p>
            <p className="mt-2 text-lg font-semibold text-emerald-900">{t("support.counterDoneTitle")}</p>
            <p className="mt-1 text-sm text-emerald-800">
              {t("support.counterDoneText", {
                old: counterResult.old != null ? counterResult.old.toLocaleString("hu-HU") : "—",
                next: counterResult.next.toLocaleString("hu-HU"),
              })}
            </p>
            <button
              onClick={() => setMode("choose")}
              className="mt-4 rounded-xl border border-emerald-300 px-4 py-2 text-sm text-emerald-800 hover:bg-emerald-100"
            >
              {t("support.doneBack")}
            </button>
          </div>
        )}

        {mode === "order" && (
          <form onSubmit={submitOrder} className="space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between">
              <p className="font-semibold">{t("support.orderTitle")}</p>
              <button type="button" onClick={() => setMode("choose")} className="text-sm text-slate-400 hover:text-slate-600">
                ← {t("support.back")}
              </button>
            </div>
            <div className="space-y-2">
              {orderItems.map((item, i) => (
                <div key={i} className="flex items-center gap-2">
                  <select
                    value={item.product_id}
                    onChange={(e) => {
                      const next = [...orderItems];
                      next[i] = { ...next[i], product_id: e.target.value };
                      setOrderItems(next);
                    }}
                    className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                  >
                    {info.products.map((p) => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>
                  <input
                    type="number"
                    min={0.5}
                    step="0.5"
                    value={item.quantity}
                    onChange={(e) => {
                      const next = [...orderItems];
                      next[i] = { ...next[i], quantity: e.target.value };
                      setOrderItems(next);
                    }}
                    className="w-20 rounded-lg border border-slate-300 px-2 py-2 text-sm"
                  />
                  <span className="w-8 text-sm text-slate-500">
                    {info.products.find((p) => p.id === item.product_id)?.unit ?? "kg"}
                  </span>
                  {orderItems.length > 1 && (
                    <button
                      type="button"
                      onClick={() => setOrderItems(orderItems.filter((_, j) => j !== i))}
                      className="flex h-7 w-7 items-center justify-center rounded-full text-red-600 hover:bg-red-50"
                    >
                      ×
                    </button>
                  )}
                </div>
              ))}
              {orderItems.length < 5 && (
                <button
                  type="button"
                  onClick={() =>
                    setOrderItems([...orderItems, { product_id: info.products[0]?.id ?? "", quantity: "1" }])
                  }
                  className="text-sm font-medium text-emerald-700 hover:underline"
                >
                  + {t("support.orderAddItem")}
                </button>
              )}
            </div>
            <label className="block text-sm">
              {t("support.orderNote")}
              <input
                value={orderNote}
                onChange={(e) => setOrderNote(e.target.value)}
                placeholder={t("support.orderNotePh")}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                {t("support.contactName")}
                <input
                  value={contactName}
                  onChange={(e) => setContactName(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                />
              </label>
              <label className="block text-sm">
                {t("support.contactPhone")}
                <input
                  value={contactPhone}
                  onChange={(e) => setContactPhone(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                />
              </label>
            </div>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <button
              disabled={busy || orderItems.length === 0}
              className="w-full rounded-xl bg-emerald-600 px-4 py-2.5 font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {busy ? t("common.saving") : t("support.orderSubmit")}
            </button>
          </form>
        )}

        {mode === "order_done" && (
          <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-6 text-center shadow-sm">
            <p className="text-3xl">🛒</p>
            <p className="mt-2 text-lg font-semibold text-emerald-900">{t("support.orderDoneTitle")}</p>
            <p className="mt-1 text-sm text-emerald-800">{t("support.orderDoneText", { order: orderNo ?? "?" })}</p>
            <button
              onClick={() => setMode("choose")}
              className="mt-4 rounded-xl border border-emerald-300 px-4 py-2 text-sm text-emerald-800 hover:bg-emerald-100"
            >
              {t("support.doneBack")}
            </button>
          </div>
        )}

        {mode === "chat" && (
          <div className="flex flex-col rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
              <p className="font-semibold">{t("support.chatTitle")}</p>
              <button onClick={() => setMode("choose")} className="text-sm text-slate-400 hover:text-slate-600">
                ← {t("support.back")}
              </button>
            </div>
            <div className="max-h-[50vh] min-h-48 space-y-3 overflow-y-auto p-4">
              {messages.length === 0 && (
                <p className="text-sm text-slate-400">{t("support.chatWelcome")}</p>
              )}
              {messages.map((m, i) => (
                <div key={i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
                  <div
                    className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-sm ${
                      m.role === "user" ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-800"
                    }`}
                  >
                    {m.content}
                  </div>
                </div>
              ))}
              {chatBusy && <p className="text-sm text-slate-400">{t("support.chatThinking")}</p>}
              <div ref={chatEndRef} />
            </div>
            <form onSubmit={sendChat} className="flex gap-2 border-t border-slate-100 p-3">
              <input
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder={t("support.chatPlaceholder")}
                className="flex-1 rounded-xl border border-slate-300 px-3 py-2 text-sm"
              />
              <button
                disabled={chatBusy || !chatInput.trim()}
                className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {t("support.chatSend")}
              </button>
            </form>
            <div className="border-t border-slate-100 px-4 py-2.5 text-center">
              <button onClick={() => setMode("ticket")} className="text-sm font-medium text-amber-700 hover:underline">
                {t("support.chatToTicket")}
              </button>
            </div>
          </div>
        )}

        {mode === "ticket" && (
          <form onSubmit={submitTicket} className="space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between">
              <p className="font-semibold">{t("support.ticketTitle")}</p>
              <button type="button" onClick={() => setMode("choose")} className="text-sm text-slate-400 hover:text-slate-600">
                ← {t("support.back")}
              </button>
            </div>
            <label className="block text-sm">
              {t("support.description")} *
              <textarea
                required
                minLength={5}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={4}
                placeholder={t("support.descriptionPh")}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                {t("support.contactName")}
                <input
                  value={contactName}
                  onChange={(e) => setContactName(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                />
              </label>
              <label className="block text-sm">
                {t("support.contactPhone")}
                <input
                  value={contactPhone}
                  onChange={(e) => setContactPhone(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                />
              </label>
            </div>
            <label className="block text-sm">
              {t("support.contactEmail")}
              <input
                type="email"
                value={contactEmail}
                onChange={(e) => setContactEmail(e.target.value)}
                placeholder="pl. nev@ceg.hu"
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <div className="text-sm">
              <p>{t("support.photos", { max: MAX_PHOTOS })}</p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {photos.map((p, i) => (
                  <div key={i} className="relative">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={p.dataUrl} alt={p.name} className="h-16 w-16 rounded-lg border border-slate-200 object-cover" />
                    <button
                      type="button"
                      onClick={() => setPhotos(photos.filter((_, j) => j !== i))}
                      className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-red-600 text-xs text-white"
                    >
                      ×
                    </button>
                  </div>
                ))}
                {photos.length < MAX_PHOTOS && (
                  <label className="flex h-16 w-16 cursor-pointer items-center justify-center rounded-lg border-2 border-dashed border-slate-300 text-2xl text-slate-400 hover:border-indigo-400 hover:text-indigo-500">
                    +
                    <input
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      capture="environment"
                      multiple
                      onChange={(e) => { addPhotos(e.target.files); e.target.value = ""; }}
                      className="hidden"
                    />
                  </label>
                )}
              </div>
            </div>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <button
              disabled={busy}
              className="w-full rounded-xl bg-amber-600 px-4 py-2.5 font-medium text-white hover:bg-amber-700 disabled:opacity-50"
            >
              {busy ? t("common.saving") : t("support.submit")}
            </button>
          </form>
        )}

        {mode === "done" && (
          <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-6 text-center shadow-sm">
            <p className="text-3xl">✅</p>
            <p className="mt-2 text-lg font-semibold text-emerald-900">{t("support.doneTitle")}</p>
            <p className="mt-1 text-sm text-emerald-800">
              {t("support.doneText", { ticket: ticketNo ?? "?" })}
            </p>
            <button
              onClick={() => { setMode("choose"); setDescription(""); setPhotos([]); setTicketNo(null); }}
              className="mt-4 rounded-xl border border-emerald-300 px-4 py-2 text-sm text-emerald-800 hover:bg-emerald-100"
            >
              {t("support.doneBack")}
            </button>
          </div>
        )}

        <footer className="pb-2 text-center text-xs text-slate-400">{t("portal.footer")}</footer>
      </div>
    </main>
  );
}
