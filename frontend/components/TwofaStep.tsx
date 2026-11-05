"use client";

/** Kétlépcsős belépés második lépése (147. kör): a jelszó után Telegramon
 *  (tartalékként e-mailben) kapott 6 jegyű kód beírása. A sima és az
 *  X-Learning belépő is ezt használja. */

import { useState } from "react";
import { ApiError, api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";

export interface TwofaStart {
  twofa_required: true;
  challenge_id: string;
  channel: "telegram" | "email";
  hint: string;
  ttl_minutes: number;
}

export function isTwofaStart(v: unknown): v is TwofaStart {
  return !!v && typeof v === "object" && (v as { twofa_required?: boolean }).twofa_required === true;
}

export default function TwofaStep({
  start,
  onDone,
  onBack,
  accent = "indigo",
}: {
  start: TwofaStart;
  onDone: () => void;
  onBack: () => void;
  accent?: "indigo" | "red";
}) {
  const { t } = useT();
  const [code, setCode] = useState("");
  const [trust, setTrust] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  async function verify(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      await api.post("/api/auth/login/verify", { challenge_id: start.challenge_id, code: code.trim(), trust_device: trust });
      onDone();
    } catch (err) {
      if (err instanceof ApiError && err.code === "auth.twofa_bad_code") {
        const left = (err.detail as { attempts_left?: number } | null)?.attempts_left;
        setError(t("twofa.badCode", { left: left ?? 0 }));
        setCode("");
      } else {
        setError(errorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setError(null);
    setInfo(null);
    try {
      await api.post("/api/auth/login/resend", { challenge_id: start.challenge_id });
      setInfo(t("twofa.resent"));
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const btn = accent === "red" ? "bg-[#E31E24] hover:bg-red-700" : "bg-indigo-600 hover:bg-indigo-700";

  return (
    <form onSubmit={verify} className="space-y-3 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="text-lg font-semibold">🔐 {t("twofa.title")}</h2>
      <p className="text-sm text-slate-600">
        {start.channel === "telegram" ? t("twofa.sentTelegram") : t("twofa.sentEmail", { hint: start.hint })}{" "}
        {t("twofa.ttl", { n: start.ttl_minutes })}
      </p>
      <label className="block text-sm">
        {t("twofa.code")}
        <input
          required
          autoFocus
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9]{6}"
          maxLength={6}
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-center text-2xl tracking-[0.4em]"
        />
      </label>
      <label className="flex items-center gap-2 text-sm text-slate-600">
        <input type="checkbox" checked={trust} onChange={(e) => setTrust(e.target.checked)} className="h-4 w-4" />
        {t("twofa.trust")}
      </label>
      {error && <p className="text-sm text-red-600">{error}</p>}
      {info && <p className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{info}</p>}
      <button disabled={busy || code.length !== 6} className={`w-full rounded-lg px-4 py-2 font-medium text-white disabled:opacity-50 ${btn}`}>
        {busy ? "…" : t("twofa.verify")}
      </button>
      <div className="flex justify-between text-xs text-slate-500">
        <button type="button" onClick={resend} className="hover:underline">
          {t("twofa.resend")}
        </button>
        <button type="button" onClick={onBack} className="hover:underline">
          {t("twofa.back")}
        </button>
      </div>
    </form>
  );
}
