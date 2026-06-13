"use client";

/** Bejelentkezett keret: fejléc + szerepkör-függő navigáció. */

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { LanguageSwitcher, useT } from "@/lib/i18n";
import type { AuthUser } from "@/lib/types";

const NAV = [
  { href: "/vezerlopult", key: "nav.dashboard", roles: ["admin", "manager"] },
  { href: "/feladatok", key: "nav.tasks", roles: ["admin", "manager"] },
  { href: "/dolgozok", key: "nav.employees", roles: ["admin", "manager"] },
  { href: "/beosztas", key: "nav.schedule", roles: ["admin", "manager"] },
  { href: "/tavollet", key: "nav.timeOff", roles: ["admin", "manager"] },
  { href: "/jelenlet", key: "nav.attendance", roles: ["admin", "manager"] },
  { href: "/keszlet", key: "nav.inventory", roles: ["admin", "manager"] },
  { href: "/beallitasok", key: "nav.settings", roles: ["admin"] },
  { href: "/beosztasom", key: "nav.mySchedule", roles: ["employee"] },
  { href: "/feladataim", key: "nav.myTasks", roles: ["employee"] },
] as const;

export default function AppShell({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const pathname = usePathname();
  const router = useRouter();
  const { t } = useT();

  useEffect(() => {
    api
      .get<AuthUser>("/api/auth/me")
      .then(setUser)
      .catch(() => router.replace("/login"))
      .finally(() => setLoading(false));
  }, [router]);

  async function logout() {
    await api.post("/api/auth/logout");
    router.replace("/login");
  }

  if (loading) {
    return <div className="p-10 text-slate-500">{t("common.loading")}</div>;
  }
  if (!user) return null;

  const items = NAV.filter((n) => (n.roles as readonly string[]).includes(user.role));

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/logo.svg" alt="iwfm — Intelligence Workforce Management" className="h-9 w-auto" />
          <nav className="flex gap-1">
            {items.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                  pathname.startsWith(item.href)
                    ? "bg-indigo-50 text-indigo-700"
                    : "text-slate-600 hover:bg-slate-100"
                }`}
              >
                {t(item.key)}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <LanguageSwitcher />
            <span className="text-slate-500">{user.display_name}</span>
            <button
              onClick={logout}
              className="rounded-lg border border-slate-300 px-3 py-1.5 text-slate-600 hover:bg-slate-100"
            >
              {t("common.logout")}
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
    </div>
  );
}
