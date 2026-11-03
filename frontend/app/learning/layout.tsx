"use client";

/** X-Learning (142. kör): a tanulói felület saját kerete — AppShell NÉLKÜL,
 *  saját arculattal. Minden dolgozó a meglévő fiókjával lép be, és csak a
 *  saját kartonját látja; a `learning` joggal bírók ugyanitt érik el a
 *  „Szerkesztés / Eredmények” részt (/learning/admin). */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { LanguageSwitcher, useT } from "@/lib/i18n";
import { usePerms } from "@/lib/perms";
import type { AuthUser } from "@/lib/types";
import LearningWordmark from "./Wordmark";

export default function LearningLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { t } = useT();
  const perms = usePerms();
  const isLogin = pathname === "/learning/login";
  const [brand, setBrand] = useState<string | null>(null);
  const [meState, setMe] = useState<AuthUser | null>(null);
  const me = isLogin ? null : meState;

  useEffect(() => {
    api
      .get<{ company_name: string | null }>("/api/settings/branding")
      .then((b) => setBrand(b.company_name))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (isLogin) return;
    let alive = true;
    api
      .get<AuthUser>("/api/auth/me")
      .then((u) => {
        if (alive) setMe(u);
      })
      .catch(() => router.replace("/learning/login"));
    return () => {
      alive = false;
    };
  }, [isLogin, pathname, router]);

  async function logout() {
    try {
      await api.post("/api/auth/logout");
    } catch {
      /* a süti így is lejár */
    }
    router.replace("/learning/login");
  }

  const linkCls = (href: string) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      pathname === href ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-200"
    }`;

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3 px-4 py-3">
          <Link href="/learning" className="flex items-center gap-3">
            <LearningWordmark />
            {brand && <span className="hidden text-xs uppercase tracking-widest text-slate-500 sm:inline">{brand}</span>}
          </Link>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {me && (
              <>
                <Link href="/learning" className={linkCls("/learning")}>
                  {t("learning.nav.my")}
                </Link>
                {perms.can("learning") && (
                  <Link href="/learning/admin" className={linkCls("/learning/admin")}>
                    {t("learning.nav.admin")}
                  </Link>
                )}
              </>
            )}
            <LanguageSwitcher />
            {me && (
              <button
                type="button"
                onClick={logout}
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-100"
                title={me.display_name}
              >
                {t("common.logout")}
              </button>
            )}
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">{children}</main>
      <footer className="mx-auto max-w-5xl px-4 pb-6 text-center text-[11px] text-slate-400">
        X-Learning · X-admin
      </footer>
    </div>
  );
}
