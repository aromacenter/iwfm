"use client";

/** Nyilvános javítási árajánlat-oldal (e-mailben küldött linkről, belépés
 *  nélkül). Az ügyfél látja a választható javítási konstrukciókat árakkal,
 *  kiválasztja a megfelelőt, megadja a nevét és jóváhagyja — utána a
 *  szerviz értesítést kap és kezdi a munkát. */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";

interface QuoteOption {
  name: string;
  price_net: number;
  price_gross: number | null;
}

interface QuoteInfo {
  serial: string;
  status: string;
  machine: string | null;
  client_name: string | null;
  company_name: string | null;
  selected_name: string | null;
  accepted_at: string | null;
  total_loss: boolean;
  survey_fee_net: number;
  survey_fee_gross: number;
  options: QuoteOption[];
}

const DECLINE = "__decline__";
const RENOUNCE = "__renounce__";

const ft = (n: number) => `${Math.round(n).toLocaleString("hu-HU")} Ft`;

export default function MunkalapAjanlatPage() {
  const { token } = useParams<{ token: string }>();
  const [info, setInfo] = useState<QuoteInfo | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [selected, setSelected] = useState<string>("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!token) return;
    api.get<QuoteInfo>(`/api/public/worksheet-quote/${token}`)
      .then(setInfo)
      .catch(() => setNotFound(true));
  }, [token]);

  async function accept() {
    if (!selected || name.trim().length < 2) {
      setError("Válassz egy konstrukciót, és add meg a neved.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/public/worksheet-quote/${token}/accept`, {
        option_name: selected === DECLINE || selected === RENOUNCE ? null : selected,
        decline: selected === DECLINE,
        renounce: selected === RENOUNCE,
        accepted_by: name.trim(),
      });
      setDone(true);
    } catch {
      setError("A jóváhagyás nem sikerült — kérjük, próbáld újra.");
    } finally {
      setBusy(false);
    }
  }

  if (notFound) {
    return (
      <main className="mx-auto max-w-lg p-8 text-center">
        <h1 className="text-xl font-semibold">Az árajánlat nem található</h1>
        <p className="mt-2 text-sm text-slate-500">
          A link érvénytelen vagy lejárt. Kérjük, vedd fel a kapcsolatot szervizünkkel.
        </p>
      </main>
    );
  }
  if (!info) {
    return <main className="mx-auto max-w-lg p-8 text-center text-slate-500">Betöltés…</main>;
  }

  const accepted = done || info.status === "accepted" || info.status === "declined";
  const renouncedResult =
    (done && selected === RENOUNCE) || (info.selected_name ?? "").includes("tulajdonjog");
  const declinedResult =
    !renouncedResult && ((done && selected === DECLINE) || info.status === "declined");

  return (
    <main className="mx-auto max-w-lg space-y-4 p-4 sm:p-8">
      <header className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <p className="text-xs uppercase tracking-wide text-slate-400">
          {info.company_name || "X-Presso"} · Javítási árajánlat
        </p>
        <h1 className="mt-1 text-2xl font-semibold">{info.serial}</h1>
        {info.machine && <p className="mt-1 text-sm text-slate-600">☕ {info.machine}</p>}
        {info.client_name && <p className="text-sm text-slate-500">{info.client_name} részére</p>}
      </header>

      {accepted ? (
        renouncedResult ? (
          <div className="rounded-2xl border border-slate-300 bg-slate-50 p-5">
            <h2 className="text-lg font-semibold text-slate-800">✔ Köszönjük, a válaszát rögzítettük.</h2>
            <p className="mt-2 text-sm text-slate-700">
              Lemondott a készülék tulajdonjogáról — a gép a szerviznél marad,
              Önnek semmilyen fizetnivalója nincs.
            </p>
          </div>
        ) : declinedResult ? (
          <div className="rounded-2xl border border-slate-300 bg-slate-50 p-5">
            <h2 className="text-lg font-semibold text-slate-800">✔ Köszönjük, a válaszát rögzítettük.</h2>
            <p className="mt-2 text-sm text-slate-700">
              A javítást nem kérte — a felmérési díj ({ft(info.survey_fee_net)} + ÁFA)
              kerül kiszámlázásra, a készüléket összeszerelve visszaadjuk.
            </p>
          </div>
        ) : (
          <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5">
            <h2 className="text-lg font-semibold text-emerald-800">✔ Köszönjük, a jóváhagyás megtörtént!</h2>
            <p className="mt-2 text-sm text-emerald-900">
              Kiválasztott konstrukció:{" "}
              <strong>{done ? selected : info.selected_name}</strong>
            </p>
            <p className="mt-1 text-sm text-emerald-900">
              Szervizünk megkapta a jóváhagyást, és megkezdi a javítást. Amint elkészült,
              értesítjük.
            </p>
          </div>
        )
      ) : info.total_loss ? (
        <>
          {/* Gazdasági totálkár: csak két opció — bevizsgálási díj VAGY lemondás */}
          <div className="rounded-2xl border border-amber-300 bg-amber-50 p-5">
            <h2 className="text-lg font-semibold text-amber-900">
              ⚠️ A készülék gazdaságosan nem javítható
            </h2>
            <p className="mt-2 text-sm text-amber-900">
              Szervizünk bevizsgálta a készüléket, és megállapította, hogy a javítás
              költsége meghaladná a gép értékét (gazdasági totálkár). Kérjük, válasszon
              az alábbi két lehetőség közül:
            </p>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="space-y-2">
              <label
                className={`flex cursor-pointer items-center justify-between gap-3 rounded-xl border p-3 transition ${
                  selected === DECLINE
                    ? "border-indigo-500 bg-indigo-50 ring-1 ring-indigo-500"
                    : "border-slate-200 hover:border-slate-300"
                }`}
              >
                <span className="flex items-center gap-3">
                  <input
                    type="radio"
                    name="option"
                    checked={selected === DECLINE}
                    onChange={() => setSelected(DECLINE)}
                    className="h-4 w-4"
                  />
                  <span className="text-sm font-medium">
                    Vállalom a bevizsgálási díj megfizetését — a gépet visszakapom
                  </span>
                </span>
                <span className="text-right text-sm">
                  <span className="font-semibold">{ft(info.survey_fee_net)}</span>
                  <span className="block text-xs text-slate-400">
                    + ÁFA (bruttó {ft(info.survey_fee_gross)})
                  </span>
                </span>
              </label>
              <label
                className={`flex cursor-pointer items-center justify-between gap-3 rounded-xl border p-3 transition ${
                  selected === RENOUNCE
                    ? "border-slate-600 bg-slate-100 ring-1 ring-slate-600"
                    : "border-slate-200 hover:border-slate-300"
                }`}
              >
                <span className="flex items-center gap-3">
                  <input
                    type="radio"
                    name="option"
                    checked={selected === RENOUNCE}
                    onChange={() => setSelected(RENOUNCE)}
                    className="h-4 w-4"
                  />
                  <span className="text-sm font-medium">
                    Lemondok a gép tulajdonjogáról — a gép a szerviznél marad
                  </span>
                </span>
                <span className="text-right text-sm">
                  <span className="font-semibold text-emerald-700">Díjmentes</span>
                  <span className="block text-xs text-slate-400">0 Ft</span>
                </span>
              </label>
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <label className="block text-sm">
              Jóváhagyó neve
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="pl. Kovács Anna"
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
            <button
              onClick={accept}
              disabled={busy}
              className="mt-3 w-full rounded-xl bg-indigo-600 px-4 py-3 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {selected === RENOUNCE
                ? "✔ Megerősítem: lemondok a gép tulajdonjogáról (díjmentes)"
                : "✔ Megerősítem: vállalom a bevizsgálási díj megfizetését"}
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
              Választható javítási konstrukciók
            </h2>
            <div className="mt-3 space-y-2">
              {info.options.map((o) => (
                <label
                  key={o.name}
                  className={`flex cursor-pointer items-center justify-between gap-3 rounded-xl border p-3 transition ${
                    selected === o.name
                      ? "border-indigo-500 bg-indigo-50 ring-1 ring-indigo-500"
                      : "border-slate-200 hover:border-slate-300"
                  }`}
                >
                  <span className="flex items-center gap-3">
                    <input
                      type="radio"
                      name="option"
                      checked={selected === o.name}
                      onChange={() => setSelected(o.name)}
                      className="h-4 w-4"
                    />
                    <span className="text-sm font-medium">{o.name}</span>
                  </span>
                  <span className="text-right text-sm">
                    <span className="font-semibold">{ft(o.price_net)}</span>
                    <span className="block text-xs text-slate-400">
                      + ÁFA {o.price_gross != null ? `(bruttó ${ft(o.price_gross)})` : ""}
                    </span>
                  </span>
                </label>
              ))}
              {/* Mindig felkínált opció: a javítás elutasítása felmérési díjjal */}
              <label
                className={`flex cursor-pointer items-center justify-between gap-3 rounded-xl border p-3 transition ${
                  selected === DECLINE
                    ? "border-rose-500 bg-rose-50 ring-1 ring-rose-500"
                    : "border-slate-200 hover:border-slate-300"
                }`}
              >
                <span className="flex items-center gap-3">
                  <input
                    type="radio"
                    name="option"
                    checked={selected === DECLINE}
                    onChange={() => setSelected(DECLINE)}
                    className="h-4 w-4"
                  />
                  <span className="text-sm font-medium">
                    Nem kérem a javítást — vállalom a felmérési díj megfizetését
                  </span>
                </span>
                <span className="text-right text-sm">
                  <span className="font-semibold">{ft(info.survey_fee_net)}</span>
                  <span className="block text-xs text-slate-400">
                    + ÁFA (bruttó {ft(info.survey_fee_gross)})
                  </span>
                </span>
              </label>
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <label className="block text-sm">
              Jóváhagyó neve
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="pl. Kovács Anna"
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
            <button
              onClick={accept}
              disabled={busy}
              className="mt-3 w-full rounded-xl bg-indigo-600 px-4 py-3 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {selected === DECLINE
                ? "✔ Megerősítem: a javítást nem kérem (felmérési díj fizetendő)"
                : "✔ A kiválasztott konstrukciót jóváhagyom"}
            </button>
            <p className="mt-2 text-center text-xs text-slate-400">
              A jóváhagyás után szervizünk megkezdi a javítást a kiválasztott
              konstrukció szerint.
            </p>
          </div>
        </>
      )}
    </main>
  );
}
