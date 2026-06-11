"use client";

/** Bejelentkezés + első indításkor admin fiók létrehozása. */

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, errorMessage } from "@/lib/api";
import type { AuthUser } from "@/lib/types";

export default function LoginPage() {
  const [mode, setMode] = useState<"login" | "bootstrap">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const router = useRouter();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const user =
        mode === "login"
          ? await api.post<AuthUser>("/api/auth/login", { email, password })
          : await api.post<AuthUser>("/api/auth/bootstrap", {
              email,
              password,
              display_name: displayName,
            });
      router.replace(user.role === "employee" ? "/beosztasom" : "/beosztas");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <h1 className="mb-1 text-center text-2xl font-bold text-indigo-700">Iwfm</h1>
        <p className="mb-6 text-center text-sm text-slate-500">
          Munkaerő-kezelés — beosztás, jelenlét, bérexport
        </p>
        <form onSubmit={submit} className="space-y-3 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-lg font-semibold">
            {mode === "login" ? "Bejelentkezés" : "Első admin létrehozása"}
          </h2>
          {mode === "bootstrap" && (
            <label className="block text-sm">
              Megjelenített név
              <input
                required
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
          )}
          <label className="block text-sm">
            Email
            <input
              required
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block text-sm">
            Jelszó {mode === "bootstrap" && <span className="text-slate-400">(min. 10 karakter)</span>}
            <input
              required
              type="password"
              minLength={mode === "bootstrap" ? 10 : 1}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
            />
          </label>
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button
            disabled={busy}
            className="w-full rounded-lg bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {busy ? "…" : mode === "login" ? "Belépés" : "Létrehozás"}
          </button>
          <button
            type="button"
            onClick={() => {
              setMode(mode === "login" ? "bootstrap" : "login");
              setError(null);
            }}
            className="w-full text-center text-xs text-slate-500 hover:text-indigo-600"
          >
            {mode === "login"
              ? "Első indítás? Admin fiók létrehozása"
              : "Vissza a bejelentkezéshez"}
          </button>
        </form>
      </div>
    </div>
  );
}
