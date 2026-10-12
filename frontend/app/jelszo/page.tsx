"use client";

/** Új jelszó beállítása az admintól kapott visszaállító linkről (b3640159).
 * A link egyszer használatos és 24 óráig él — publikus "elfelejtett jelszó"
 * oldal szándékosan nincs, csak admin küldhet ilyet. */

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import BrandLogo from "@/components/BrandLogo";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";

function JelszoForm() {
  const search = useSearchParams();
  const token = search.get("token") ?? "";
  const router = useRouter();
  const { t } = useT();
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (pw.length < 10) { setError(t("pwreset.tooShort")); return; }
    if (pw !== pw2) { setError(t("pwreset.mismatch")); return; }
    setBusy(true);
    try {
      await api.post("/api/auth/reset-password", { token, password: pw });
      setDone(true);
      setTimeout(() => router.replace("/login"), 2500);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex justify-center">
          <BrandLogo size="lg" />
        </div>
        {done ? (
          <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-6 text-center">
            <p className="text-lg font-semibold text-emerald-800">✅ {t("pwreset.done")}</p>
            <p className="mt-1 text-sm text-emerald-700">{t("pwreset.doneHint")}</p>
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-3 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <h2 className="text-lg font-semibold">🔑 {t("pwreset.title")}</h2>
            {!token && <p className="text-sm text-rose-600">{t("pwreset.noToken")}</p>}
            <label className="block text-sm">
              {t("pwreset.newPassword")}
              <input
                type="password"
                required
                minLength={10}
                value={pw}
                onChange={(e) => setPw(e.target.value)}
                autoComplete="new-password"
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="block text-sm">
              {t("pwreset.again")}
              <input
                type="password"
                required
                value={pw2}
                onChange={(e) => setPw2(e.target.value)}
                autoComplete="new-password"
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <p className="text-xs text-slate-500">{t("pwreset.hint")}</p>
            {error && <p className="text-sm text-rose-600">{error}</p>}
            <button
              disabled={busy || !token}
              className="w-full rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {busy ? t("common.saving") : t("pwreset.submit")}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

export default function JelszoPage() {
  return (
    <Suspense>
      <JelszoForm />
    </Suspense>
  );
}
