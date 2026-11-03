"use client";

/** X-Learning belépő: a dolgozó a meglévő céges fiókjával (e-mail vagy
 *  felhasználónév + jelszó) lép be — fiókot NEM hozunk létre automatikusan. */

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { AuthUser } from "@/lib/types";
import LearningWordmark from "../Wordmark";

export default function LearningLoginPage() {
  const [mode, setMode] = useState<"login" | "forgot">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [forgotDone, setForgotDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const router = useRouter();
  const { t } = useT();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "forgot") {
        await api.post("/api/auth/forgot", { email });
        setForgotDone(true);
        return;
      }
      await api.post<AuthUser>("/api/auth/login", { email, password });
      router.replace("/learning");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const inputCls = "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2";

  return (
    <div className="mx-auto mt-6 w-full max-w-sm">
      <div className="mb-6 flex justify-center">
        <LearningWordmark size="lg" />
      </div>
      <form onSubmit={submit} className="space-y-3 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-lg font-semibold">{mode === "login" ? t("learning.login.title") : t("learning.login.forgotTitle")}</h2>
        <p className="text-xs text-slate-500">{mode === "login" ? t("learning.login.hint") : t("learning.login.forgotHint")}</p>
        <label className="block text-sm">
          {mode === "login" ? t("learning.login.identifier") : t("learning.login.email")}
          <input
            required
            type={mode === "login" ? "text" : "email"}
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={inputCls}
          />
        </label>
        {mode === "login" && (
          <label className="block text-sm">
            {t("learning.login.password")}
            <input
              required
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={inputCls}
            />
          </label>
        )}
        {error && <p className="text-sm text-red-600">{error}</p>}
        {forgotDone && mode === "forgot" && (
          <p className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800">{t("learning.login.forgotDone")}</p>
        )}
        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-lg bg-[#E31E24] px-4 py-2 font-medium text-white hover:bg-red-700 disabled:opacity-60"
        >
          {mode === "login" ? t("learning.login.submit") : t("learning.login.forgotSubmit")}
        </button>
        <button
          type="button"
          onClick={() => {
            setMode(mode === "login" ? "forgot" : "login");
            setError(null);
            setForgotDone(false);
          }}
          className="w-full text-center text-xs text-slate-500 hover:underline"
        >
          {mode === "login" ? t("learning.login.forgotLink") : t("learning.login.backToLogin")}
        </button>
      </form>
      <p className="mt-4 text-center text-xs text-slate-400">{t("learning.login.footer")}</p>
    </div>
  );
}
