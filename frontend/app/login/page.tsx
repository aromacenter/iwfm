"use client";

/** Bejelentkezés + első indításkor admin fiók létrehozása. */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import BrandLogo from "@/components/BrandLogo";
import { api, errorMessage } from "@/lib/api";
import { LanguageSwitcher, useT } from "@/lib/i18n";
import type { AuthUser } from "@/lib/types";

export default function LoginPage() {
  const [mode, setMode] = useState<"login" | "bootstrap" | "forgot">("login");
  // az üdvözlő e-mail linkje: /login?bootstrap=1&token=… → egyből az első
  // admin létrehozása, a token láthatatlanul megy a kéréssel
  const [bootToken, setBootToken] = useState<string | null>(null);
  const [forgotDone, setForgotDone] = useState(false);
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    if (q.get("bootstrap") === "1") {
      setMode("bootstrap");
      setBootToken(q.get("token"));
    }
  }, []);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
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
      const user =
        mode === "login"
          ? await api.post<AuthUser>("/api/auth/login", { email, password })
          : await api.post<AuthUser>("/api/auth/bootstrap", {
              email,
              password,
              display_name: displayName,
              token: bootToken ?? undefined,
            });
      void user;
      // friss telepítésnél (bootstrap) a beüzemelő varázsló fogad
      router.replace(mode === "bootstrap" ? "/onboarding" : "/naptar");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-2 flex justify-end">
          <LanguageSwitcher />
        </div>
        <div className="mb-6 flex justify-center">
          <BrandLogo size="lg" />
        </div>
        <form onSubmit={submit} className="space-y-3 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-lg font-semibold">
            {mode === "login" ? t("login.title") : mode === "forgot" ? t("login.forgotTitle") : t("login.bootstrapTitle")}
          </h2>
          {mode === "bootstrap" && bootToken && (
            <p className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800">{t("login.welcomeHint")}</p>
          )}
          {mode === "forgot" && (
            <p className="text-xs text-slate-500">{t("login.forgotHint")}</p>
          )}
          {mode === "bootstrap" && (
            <label className="block text-sm">
              {t("login.displayName")}
              <input
                required
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
          )}
          <label className="block text-sm">
            {mode === "login" ? t("login.identifier") : t("login.email")}
            <input
              required
              // belépés email-lel VAGY felhasználónévvel — type=email tiltaná
              // a sima felhasználónevet (bootstrapnél/elfelejtett jelszónál szigorú email)
              type={mode === "login" ? "text" : "email"}
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
            />
          </label>
          {mode !== "forgot" && (
          <label className="block text-sm">
            {t("login.password")}{" "}
            {mode === "bootstrap" && <span className="text-slate-400">{t("login.passwordHint")}</span>}
            <input
              required
              type="password"
              minLength={mode === "bootstrap" ? 10 : 1}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
            />
          </label>
          )}
          {error && <p className="text-sm text-red-600">{error}</p>}
          {forgotDone && mode === "forgot" && (
            <p className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{t("login.forgotSent")}</p>
          )}
          <button
            disabled={busy}
            className="w-full rounded-lg bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {busy ? "…" : mode === "login" ? t("login.signIn") : mode === "forgot" ? t("login.forgotSend") : t("login.create")}
          </button>
          {mode === "login" && (
            <button
              type="button"
              onClick={() => { setMode("forgot"); setError(null); setForgotDone(false); }}
              className="w-full text-center text-xs text-slate-500 hover:text-indigo-600"
            >
              {t("login.forgotLink")}
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              setMode(mode === "login" ? "bootstrap" : "login");
              setError(null);
            }}
            className="w-full text-center text-xs text-slate-500 hover:text-indigo-600"
          >
            {mode === "login" ? t("login.toBootstrap") : t("login.toLogin")}
          </button>
        </form>
        <a
          href="/ora"
          className="mt-4 block text-center text-sm text-slate-500 hover:text-indigo-600"
        >
          {t("login.kioskLink")}
        </a>
      </div>
    </div>
  );
}
