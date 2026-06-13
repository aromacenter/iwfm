"use client";

/** Bejelentkezett keret: függőleges, témakör szerint csoportosított oldalsáv
 *  + szerepkör-függő navigáció. Mobilon kihúzható (hamburger). */

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { LanguageSwitcher, useT } from "@/lib/i18n";
import type { AuthUser } from "@/lib/types";

type Role = "admin" | "manager" | "employee";

interface NavItem {
  href: string;
  key: string;
  roles: Role[];
}

interface NavGroup {
  labelKey: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    labelKey: "nav.groups.overview",
    items: [{ href: "/vezerlopult", key: "nav.dashboard", roles: ["admin", "manager"] }],
  },
  {
    labelKey: "nav.groups.operations",
    items: [
      { href: "/feladatok", key: "nav.tasks", roles: ["admin", "manager"] },
      { href: "/beosztas", key: "nav.schedule", roles: ["admin", "manager"] },
      { href: "/jelenlet", key: "nav.attendance", roles: ["admin", "manager"] },
      { href: "/tavollet", key: "nav.timeOff", roles: ["admin", "manager"] },
    ],
  },
  {
    labelKey: "nav.groups.masterData",
    items: [
      { href: "/dolgozok", key: "nav.employees", roles: ["admin", "manager"] },
      { href: "/partnerek", key: "nav.partners", roles: ["admin", "manager"] },
      { href: "/keszlet", key: "nav.inventory", roles: ["admin", "manager"] },
    ],
  },
  {
    labelKey: "nav.groups.system",
    items: [{ href: "/beallitasok", key: "nav.settings", roles: ["admin"] }],
  },
  {
    labelKey: "nav.groups.personal",
    items: [
      { href: "/beosztasom", key: "nav.mySchedule", roles: ["employee"] },
      { href: "/feladataim", key: "nav.myTasks", roles: ["employee"] },
    ],
  },
];

export default function AppShell({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [mobileOpen, setMobileOpen] = useState(false);
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

  // Útvonalváltáskor a mobil oldalsáv csukódjon be.
  useEffect(() => setMobileOpen(false), [pathname]);

  async function logout() {
    await api.post("/api/auth/logout");
    router.replace("/login");
  }

  if (loading) {
    return <div className="p-10 text-slate-500">{t("common.loading")}</div>;
  }
  if (!user) return null;

  const groups = NAV_GROUPS.map((g) => ({
    ...g,
    items: g.items.filter((n) => n.roles.includes(user.role as Role)),
  })).filter((g) => g.items.length > 0);

  const sidebar = (
    <div className="flex h-full flex-col">
      <div className="flex h-16 items-center px-5">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/logo.svg" alt="iwfm — Intelligence Workforce Management" className="h-8 w-auto" />
      </div>
      <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-2">
        {groups.map((group) => (
          <div key={group.labelKey}>
            <p className="px-3 pb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
              {t(group.labelKey)}
            </p>
            <div className="space-y-0.5">
              {group.items.map((item) => {
                const active = pathname.startsWith(item.href);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`block rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                      active
                        ? "bg-indigo-50 text-indigo-700"
                        : "text-slate-600 hover:bg-slate-100"
                    }`}
                  >
                    {t(item.key)}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
      <div className="space-y-3 border-t border-slate-200 px-5 py-4">
        <LanguageSwitcher />
        <div className="text-sm text-slate-500">{user.display_name}</div>
        <button
          onClick={logout}
          className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
        >
          {t("common.logout")}
        </button>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen lg:flex">
      {/* Asztali, fix oldalsáv */}
      <aside className="hidden w-60 shrink-0 border-r border-slate-200 bg-white lg:block">
        <div className="sticky top-0 h-screen">{sidebar}</div>
      </aside>

      {/* Mobil fejléc */}
      <header className="flex h-14 items-center gap-3 border-b border-slate-200 bg-white px-4 lg:hidden">
        <button
          onClick={() => setMobileOpen(true)}
          className="rounded-lg p-2 text-slate-600 hover:bg-slate-100"
          aria-label={t("nav.openMenu")}
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/logo.svg" alt="iwfm" className="h-7 w-auto" />
      </header>

      {/* Mobil oldalsáv (slide-over) */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setMobileOpen(false)} />
          <aside className="absolute left-0 top-0 h-full w-64 bg-white shadow-xl">{sidebar}</aside>
        </div>
      )}

      <main className="min-w-0 flex-1">
        <div className="mx-auto max-w-6xl px-4 py-6">{children}</div>
      </main>
    </div>
  );
}
