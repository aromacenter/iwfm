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
  options: QuoteOption[];
}

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
        option_name: selected,
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

  const accepted = done || info.status === "accepted";

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
              ✔ A kiválasztott konstrukciót jóváhagyom
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
