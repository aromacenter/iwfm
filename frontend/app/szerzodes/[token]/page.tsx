"use client";

/** Nyilvános szerződés-aláíró oldal (emailben küldött linkről nyílik, belépés
 *  nélkül). A vevő elolvassa a szerződés szövegét, beírja a nevét, aláír
 *  ujjal/egérrel — mobilon, tableten és számítógépen egyaránt. Aláírás után
 *  a partner automatikusan létrejön a rendszerben. */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import SignatureCanvas from "@/components/SignatureCanvas";
import { api, ApiError } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface ContractInfo {
  serial: string;
  status: string;
  company_name: string;
  contact_name: string;
  machine_type_name: string | null;
  contract_text: string | null;
  signed_at: string | null;
  signed_name: string | null;
}

export default function SzerzodesPage() {
  const { token } = useParams<{ token: string }>();
  const { t } = useT();
  const [info, setInfo] = useState<ContractInfo | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [signature, setSignature] = useState<string | null>(null);
  const [signedName, setSignedName] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!token) return;
    api.get<ContractInfo>(`/api/public/contract/${token}`)
      .then((data) => {
        setInfo(data);
        setSignedName(data.contact_name ?? "");
      })
      .catch(() => setNotFound(true));
  }, [token]);

  async function sign() {
    if (!signature || !signedName.trim() || !accepted) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/public/contract/${token}/sign`, {
        signature,
        signed_name: signedName.trim(),
      });
      setDone(true);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? t("quote.alreadySigned")
          : t("quote.signError")
      );
    } finally {
      setBusy(false);
    }
  }

  const card = "mx-auto w-full max-w-3xl rounded-2xl border border-slate-200 bg-white shadow-sm";

  if (notFound) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-100 p-4">
        <div className={card + " p-8 text-center"}>
          <div className="mb-3 text-4xl">🔍</div>
          <p className="text-slate-600">{t("quote.contractNotFound")}</p>
        </div>
      </main>
    );
  }
  if (!info) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-100 p-4">
        <div className="text-slate-400">…</div>
      </main>
    );
  }

  if (done || info.status === "signed") {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-100 p-4">
        <div className={card + " p-8 text-center"}>
          <div className="mb-3 text-5xl">✅</div>
          <h1 className="mb-2 text-2xl font-bold text-slate-900">{t("quote.signedTitle")}</h1>
          <p className="text-slate-600">{t("quote.signedText")}</p>
          <div className="mt-4 inline-block rounded-lg bg-emerald-50 px-4 py-2 font-mono text-sm font-semibold text-emerald-700">
            {info.serial}
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-100 px-4 py-8 pb-16">
      <div className={card + " overflow-hidden"}>
        <div className="border-b border-slate-200 bg-slate-50 px-6 py-4">
          <h1 className="text-lg font-bold text-slate-900">{t("quote.contractTitle")}</h1>
          <p className="text-sm text-slate-500">
            {info.serial} · {info.company_name}
            {info.machine_type_name && <> · {info.machine_type_name}</>}
          </p>
        </div>
        <div className="max-h-[55vh] overflow-y-auto px-6 py-5">
          <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-slate-800">
            {info.contract_text}
          </pre>
        </div>
      </div>

      <div className={card + " mt-6 p-6"}>
        <h2 className="mb-3 text-base font-semibold text-slate-900">{t("quote.signSection")}</h2>
        <label className="mb-4 block text-sm">
          <span className="mb-1 block text-slate-600">{t("quote.signerName")} *</span>
          <input
            value={signedName}
            onChange={(e) => setSignedName(e.target.value)}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm focus:border-indigo-500 focus:outline-none"
          />
        </label>
        <SignatureCanvas label={t("quote.signature")} onChange={setSignature} />
        <label className="mt-4 flex items-start gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={accepted}
            onChange={(e) => setAccepted(e.target.checked)}
            className="mt-0.5"
          />
          {t("quote.acceptTerms")}
        </label>
        {error && (
          <div className="mt-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}
        <button
          onClick={sign}
          disabled={busy || !signature || !signedName.trim() || !accepted}
          className="mt-4 w-full rounded-xl bg-indigo-600 px-6 py-3.5 text-base font-semibold text-white shadow-sm transition hover:bg-indigo-700 disabled:opacity-40"
        >
          {busy ? "…" : t("quote.signButton")}
        </button>
      </div>
    </main>
  );
}
