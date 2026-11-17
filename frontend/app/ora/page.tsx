"use client";

/** Blokkoló-terminál (kiosk) — bejelentkezés nélküli oldal.
 * A dolgozó beüti a 6 jegyű törzsszámát: a rendszer automatikusan vált
 * beblokkolás és kiblokkolás között. Falra szerelt tabletre / pultgépre való. */

import { useCallback, useEffect, useState } from "react";
import SignatureCanvas from "@/components/SignatureCanvas";
import { api, errorMessage, ApiError } from "@/lib/api";
import { LanguageSwitcher, useT } from "@/lib/i18n";

interface KioskResult {
  action: "in" | "out";
  employee_name: string;
  at: string;
  worked_minutes: number | null;
}

interface KioskPreview {
  action: "in" | "out";
  employee_name: string;
  signature_required: boolean;
}

export default function OraPage() {
  const [code, setCode] = useState("");
  const [result, setResult] = useState<KioskResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // 160. kör: aláírás-lépés (ha a cég kéri) — a kód után, a blokkolás előtt
  const [pending, setPending] = useState<{ code: string; preview: KioskPreview } | null>(null);
  const [signature, setSignature] = useState<string | null>(null);
  const { t, lang } = useT();

  const submit = useCallback(
    async (fullCode: string, sig?: string | null) => {
      setBusy(true);
      setError(null);
      try {
        if (sig === undefined) {
          const preview = await api.post<KioskPreview>("/api/kiosk/preview", { code: fullCode });
          if (preview.signature_required) {
            setPending({ code: fullCode, preview });
            setSignature(null);
            setCode("");
            return;
          }
        }
        const res = await api.post<KioskResult>("/api/kiosk/clock", { code: fullCode, signature: sig ?? null });
        setPending(null);
        setSignature(null);
        setResult(res);
        setCode("");
        // 6 mp után tűnjön el az üdvözlés — jöhet a következő dolgozó
        setTimeout(() => setResult(null), 6000);
      } catch (err) {
        if (err instanceof ApiError && err.code === "kiosk.unknown_code") {
          setError(t("kiosk.unknownCode"));
        } else {
          setError(errorMessage(err));
        }
        setCode("");
        setTimeout(() => setError(null), 4000);
      } finally {
        setBusy(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  const press = useCallback(
    (digit: string) => {
      if (busy || pending) return;
      setResult(null);
      const next = (code + digit).slice(0, 6);
      setCode(next);
      if (next.length === 6) void submit(next);
    },
    [busy, pending, code, submit]
  );

  // Fizikai billentyűzet támogatása (USB numpad a terminálon)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (/^\d$/.test(e.key)) press(e.key);
      if (e.key === "Backspace") setCode((c) => c.slice(0, -1));
      if (e.key === "Escape") setCode("");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [press]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-slate-900 px-4 text-white">
      <div className="mb-3 self-end pr-2">
        <LanguageSwitcher dark />
      </div>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/logo.svg" alt="iwfm" className="mb-2 h-16 w-auto rounded-xl bg-white/95 px-3 py-1" />
      <h1 className="mb-1 text-xl font-bold text-indigo-300">{t("kiosk.title")}</h1>
      <p className="mb-6 text-sm text-slate-400">{t("kiosk.subtitle")}</p>

      {pending && (
        <div className="mb-6 w-full max-w-md rounded-2xl bg-white p-4 text-slate-900">
          <p className="text-lg font-semibold">{pending.preview.employee_name}</p>
          <p className="mb-2 text-sm text-slate-600">{pending.preview.action === "in" ? t("kiosk.signIn") : t("kiosk.signOut")}</p>
          <SignatureCanvas label={t("kiosk.signHint")} onChange={setSignature} />
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              disabled={busy || !signature}
              onClick={() => { if (!signature) { setError(t("kiosk.signMissing")); return; } void submit(pending.code, signature); }}
              className="flex-1 rounded-xl bg-emerald-600 py-3 font-semibold text-white disabled:opacity-50"
            >
              {t("kiosk.signSend")}
            </button>
            <button type="button" onClick={() => { setPending(null); setSignature(null); }} className="rounded-xl border border-slate-300 px-4 py-3 text-slate-700">
              {t("kiosk.signCancel")}
            </button>
          </div>
        </div>
      )}

      {/* Kód kijelző */}
      <div className="mb-5 flex gap-2">
        {Array.from({ length: 6 }, (_, i) => (
          <div
            key={i}
            className={`flex h-14 w-11 items-center justify-center rounded-xl border-2 text-2xl font-bold ${
              i < code.length ? "border-indigo-400 bg-slate-800" : "border-slate-700 bg-slate-800/50"
            }`}
          >
            {i < code.length ? "•" : ""}
          </div>
        ))}
      </div>

      {/* Visszajelzés */}
      <div className="mb-5 flex h-20 w-full max-w-sm items-center justify-center text-center">
        {result ? (
          <div
            className={`w-full rounded-2xl px-4 py-3 ${
              result.action === "in" ? "bg-emerald-600" : "bg-sky-600"
            }`}
          >
            <p className="text-lg font-semibold">
              {result.action === "in" ? t("kiosk.clockedIn") : t("kiosk.clockedOut")} — {result.employee_name}
            </p>
            <p className="text-sm opacity-90">
              {new Date(result.at).toLocaleTimeString(lang === "hu" ? "hu-HU" : "en-GB", { hour: "2-digit", minute: "2-digit" })}
              {result.worked_minutes !== null &&
                ` · ${t("kiosk.worked", { hours: (result.worked_minutes / 60).toFixed(1) })}`}
            </p>
          </div>
        ) : error ? (
          <p className="w-full rounded-2xl bg-red-600 px-4 py-3 font-medium">{error}</p>
        ) : (
          <p className="text-slate-500">{busy ? t("kiosk.processing") : " "}</p>
        )}
      </div>

      {/* Számpad */}
      <div className="grid w-full max-w-xs grid-cols-3 gap-3">
        {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => (
          <button
            key={d}
            onClick={() => press(d)}
            className="rounded-2xl bg-slate-800 py-5 text-2xl font-semibold hover:bg-slate-700 active:bg-indigo-600"
          >
            {d}
          </button>
        ))}
        <button
          onClick={() => setCode("")}
          className="rounded-2xl bg-slate-800 py-5 text-sm font-medium text-slate-400 hover:bg-slate-700"
        >
          {t("kiosk.clear")}
        </button>
        <button
          onClick={() => press("0")}
          className="rounded-2xl bg-slate-800 py-5 text-2xl font-semibold hover:bg-slate-700 active:bg-indigo-600"
        >
          0
        </button>
        <button
          onClick={() => setCode((c) => c.slice(0, -1))}
          className="rounded-2xl bg-slate-800 py-5 text-xl text-slate-400 hover:bg-slate-700"
        >
          ⌫
        </button>
      </div>

      <a href="/login" className="mt-8 text-xs text-slate-600 hover:text-slate-400">
        {t("kiosk.adminLink")}
      </a>
    </div>
  );
}
