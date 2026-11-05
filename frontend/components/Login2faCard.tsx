"use client";

/** Beállítások → Jogosultságok: kétlépcsős belépés (jelszó + Telegram-kód) —
 *  mód-választó és a csatornák állapota (147. kör). */

import { useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface Twofa {
  mode: "off" | "linked" | "required";
  users: number;
  telegram_linked: number;
  with_email: number;
  telegram_configured: boolean;
  smtp_configured: boolean;
}

export default function Login2faCard() {
  const { t } = useT();
  const [data, setData] = useState<Twofa | null>(null);
  const [mode, setMode] = useState<Twofa["mode"]>("off");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<Twofa>("/api/settings/login-2fa")
      .then((d) => {
        setData(d);
        setMode(d.mode);
      })
      .catch(() => {});
  }, []);

  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      const d = await api.put<Twofa>("/api/settings/login-2fa", { mode });
      setData(d);
      setMsg(t("common.saved"));
    } catch (err) {
      setMsg(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!data) return null;
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-semibold">🔐 {t("twofa.settingsTitle")}</h2>
      <p className="text-sm text-slate-600">{t("twofa.settingsHint")}</p>
      <div className="space-y-1.5">
        {(["off", "linked", "required"] as const).map((m) => (
          <label key={m} className={`flex cursor-pointer items-start gap-2 rounded-xl border p-3 text-sm ${mode === m ? "border-indigo-400 bg-indigo-50" : "border-slate-200"}`}>
            <input type="radio" name="twofa-mode" checked={mode === m} onChange={() => setMode(m)} className="mt-1" />
            <span>
              <span className="font-medium">{t(`twofa.mode.${m}`)}</span>
              <span className="block text-xs text-slate-500">{t(`twofa.modeHint.${m}`)}</span>
            </span>
          </label>
        ))}
      </div>
      <p className="text-xs text-slate-500">
        {t("twofa.stats", { linked: data.telegram_linked, users: data.users, email: data.with_email })}{" "}
        {data.telegram_configured ? "✅ Telegram-bot" : `⚠️ ${t("twofa.noBot")}`} · {data.smtp_configured ? "✅ e-mail" : `⚠️ ${t("twofa.noSmtp")}`}
      </p>
      <div className="flex items-center gap-3">
        <button type="button" disabled={busy || mode === data.mode} onClick={save} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
          {t("common.save")}
        </button>
        {msg && <span className="text-sm text-slate-600">{msg}</span>}
      </div>
    </div>
  );
}
