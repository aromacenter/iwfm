"use client";

/** Bejelentkezett keret: függőleges, témakör szerint csoportosított oldalsáv
 *  + szerepkör-függő navigáció. Mobilon kihúzható (hamburger). */

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import AssistantChat from "@/components/AssistantChat";
import { api, errorMessage } from "@/lib/api";
import { LanguageSwitcher, useT } from "@/lib/i18n";
import type { AuthUser } from "@/lib/types";

interface NavItem {
  href: string;
  key: string;
  perm: string | "admin-only";
  icon: string;
}

interface NavGroup {
  labelKey: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    labelKey: "nav.groups.overview",
    items: [{ href: "/vezerlopult", key: "nav.dashboard", perm: "dashboard", icon: "📊" }],
  },
  {
    labelKey: "nav.groups.operations",
    items: [
      { href: "/feladatok", key: "nav.tasks", perm: "tasks", icon: "🗂️" },
      { href: "/szerviz", key: "nav.service", perm: "service", icon: "🔧" },
      { href: "/tudasbazis", key: "nav.kb", perm: "service", icon: "📚" },
      { href: "/beosztas", key: "nav.schedule", perm: "schedule", icon: "🗓️" },
      { href: "/jelenlet", key: "nav.attendance", perm: "attendance", icon: "⏱️" },
      { href: "/tavollet", key: "nav.timeOff", perm: "timeoff", icon: "🏖️" },
    ],
  },
  {
    labelKey: "nav.groups.masterData",
    items: [
      { href: "/dolgozok", key: "nav.employees", perm: "employees", icon: "👥" },
      { href: "/partnerek", key: "nav.partners", perm: "partners", icon: "🤝" },
      { href: "/szerzodesek", key: "nav.contracts", perm: "partners", icon: "📜" },
      { href: "/ajanlatkeresek", key: "nav.quotes", perm: "partners", icon: "📨" },
      { href: "/gepek", key: "nav.inventory", perm: "machines", icon: "☕" },
      { href: "/termekek", key: "nav.products", perm: "products", icon: "📦" },
      { href: "/raktar", key: "nav.warehouse", perm: "settlements", icon: "🏬" },
    ],
  },
  {
    labelKey: "nav.groups.billing",
    items: [
      { href: "/elszamolas", key: "nav.settlement", perm: "settlements", icon: "🧾" },
      { href: "/uzletkoto", key: "nav.agentReport", perm: "agent_report", icon: "💼" },
    ],
  },
  {
    labelKey: "nav.groups.system",
    items: [
      { href: "/import-export", key: "nav.importExport", perm: "import_export", icon: "🔄" },
      { href: "/automatizalasok", key: "nav.automation", perm: "admin-only", icon: "⚡" },
      { href: "/naplo", key: "nav.audit", perm: "admin-only", icon: "📋" },
      { href: "/beallitasok", key: "nav.settings", perm: "admin-only", icon: "⚙️" },
    ],
  },
  {
    labelKey: "nav.groups.personal",
    items: [
      { href: "/beosztasom", key: "nav.mySchedule", perm: "my_schedule", icon: "📅" },
      { href: "/feladataim", key: "nav.myTasks", perm: "my_tasks", icon: "✅" },
    ],
  },
];

export default function AppShell({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [pwOpen, setPwOpen] = useState(false);
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" });
  const [pwBusy, setPwBusy] = useState(false);
  const [pwErr, setPwErr] = useState<string | null>(null);
  const [pwMsg, setPwMsg] = useState<string | null>(null);
  const [theme, setTheme] = useState<"light" | "dark" | "system">("system");
  const pathname = usePathname();
  const router = useRouter();
  const { t } = useT();

  useEffect(() => {
    const stored = localStorage.getItem("iwfm-theme");
    if (stored === "light" || stored === "dark" || stored === "system") setTheme(stored);
  }, []);

  function applyTheme(next: "light" | "dark" | "system") {
    setTheme(next);
    localStorage.setItem("iwfm-theme", next);
    const dark =
      next === "dark" ||
      (next === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.classList.toggle("dark", dark);
  }

  function cycleTheme() {
    applyTheme(theme === "light" ? "dark" : theme === "dark" ? "system" : "light");
  }

  useEffect(() => {
    api
      .get<AuthUser>("/api/auth/me")
      .then(setUser)
      .catch(() => router.replace("/login"))
      .finally(() => setLoading(false));
  }, [router]);

  // Felhasználónkénti szekció-sorrend (▲▼ a csoportcímeken).
  const [groupOrder, setGroupOrder] = useState<string[]>([]);
  useEffect(() => {
    if (!user) return;
    try {
      const saved = localStorage.getItem(`iwfm-nav-order:${user.id}`);
      if (saved) setGroupOrder(JSON.parse(saved) as string[]);
    } catch {
      /* sérült mentés — marad az alap sorrend */
    }
  }, [user]);

  // A mentett sorrend a LÁTHATÓ szekciókra vonatkozik; az újak a végére kerülnek.
  function sortBySaved(visible: string[]): string[] {
    const saved = groupOrder.filter((k) => visible.includes(k));
    return [...saved, ...visible.filter((k) => !saved.includes(k))];
  }

  function moveGroup(visibleKeys: string[], labelKey: string, dir: -1 | 1) {
    if (!user) return;
    const current = [...visibleKeys];
    const idx = current.indexOf(labelKey);
    const to = idx + dir;
    if (idx < 0 || to < 0 || to >= current.length) return;
    [current[idx], current[to]] = [current[to], current[idx]];
    setGroupOrder(current);
    try {
      localStorage.setItem(`iwfm-nav-order:${user.id}`, JSON.stringify(current));
    } catch {
      /* tárhely-hiba nem kritikus */
    }
  }

  // Útvonalváltáskor a mobil oldalsáv csukódjon be.
  useEffect(() => setMobileOpen(false), [pathname]);

  async function logout() {
    await api.post("/api/auth/logout");
    router.replace("/login");
  }

  function openPassword() {
    setPw({ current: "", next: "", confirm: "" });
    setPwErr(null);
    setPwMsg(null);
    setPwOpen(true);
  }

  async function changePassword(e: React.FormEvent) {
    e.preventDefault();
    setPwErr(null);
    if (pw.next !== pw.confirm) {
      setPwErr(t("account.mismatch"));
      return;
    }
    setPwBusy(true);
    try {
      await api.post("/api/me/password", { current_password: pw.current, new_password: pw.next });
      setPwMsg(t("account.changed"));
      setPw({ current: "", next: "", confirm: "" });
    } catch (err) {
      setPwErr(errorMessage(err));
    } finally {
      setPwBusy(false);
    }
  }

  if (loading) {
    return <div className="p-10 text-slate-500">{t("common.loading")}</div>;
  }
  if (!user) return null;

  const perms = user.permissions ?? [];
  const visibleGroups = NAV_GROUPS.map((g) => ({
    ...g,
    items: g.items.filter((n) =>
      n.perm === "admin-only" ? user.role === "admin" : perms.includes(n.perm),
    ),
  })).filter((g) => g.items.length > 0);
  const keys = sortBySaved(visibleGroups.map((g) => g.labelKey));
  const groups = [...visibleGroups].sort(
    (a, b) => keys.indexOf(a.labelKey) - keys.indexOf(b.labelKey),
  );

  const sidebar = (
    <div className="flex h-full flex-col">
      <div className="flex h-16 items-center px-5">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/logo.svg" alt="iwfm — Intelligence Workforce Management" className="h-11 w-auto" />
      </div>
      <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-2">
        {groups.map((group, gi) => (
          <div key={group.labelKey} className="group/nav">
            <p className="flex items-center justify-between px-3 pb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
              <span>{t(group.labelKey)}</span>
              <span className="flex gap-0.5 opacity-60 transition-opacity sm:opacity-0 sm:group-hover/nav:opacity-100">
                {gi > 0 && (
                  <button
                    onClick={() => moveGroup(keys, group.labelKey, -1)}
                    title={t("nav.moveUp")}
                    className="rounded px-1 leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                  >
                    ▲
                  </button>
                )}
                {gi < groups.length - 1 && (
                  <button
                    onClick={() => moveGroup(keys, group.labelKey, 1)}
                    title={t("nav.moveDown")}
                    className="rounded px-1 leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                  >
                    ▼
                  </button>
                )}
              </span>
            </p>
            <div className="space-y-0.5">
              {group.items.map((item) => {
                const active = pathname.startsWith(item.href);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                      active
                        ? "bg-indigo-50 text-indigo-700"
                        : "text-slate-600 hover:bg-slate-100"
                    }`}
                  >
                    <span aria-hidden className={`w-5 text-center text-base leading-none ${active ? "" : "opacity-80 grayscale-[35%]"}`}>
                      {item.icon}
                    </span>
                    {t(item.key)}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
      <div className="space-y-3 border-t border-slate-200 px-5 py-4">
        <div className="flex items-center justify-between gap-2">
          <LanguageSwitcher />
          <button
            onClick={cycleTheme}
            title={`${t("nav.theme")}: ${t(`nav.themes.${theme}`)}`}
            aria-label={t("nav.theme")}
            className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
          >
            {theme === "light" ? "☀️" : theme === "dark" ? "🌙" : "🖥️"}
          </button>
        </div>
        <div className="text-sm text-slate-500">{user.display_name}</div>
        <button
          onClick={openPassword}
          className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
        >
          {t("account.password")}
        </button>
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
        <img src="/logo.svg" alt="iwfm" className="h-9 w-auto" />
      </header>

      {/* Mobil oldalsáv (slide-over) */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setMobileOpen(false)} />
          <aside className="absolute left-0 top-0 h-full w-64 bg-white shadow-xl">{sidebar}</aside>
        </div>
      )}

      <main className="min-w-0 flex-1">
        <div className="mx-auto max-w-6xl px-4 py-6">
          {children}
        </div>
      </main>

      <AssistantChat />

      {pwOpen && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) setPwOpen(false); }}
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4"
        >
          <form onSubmit={changePassword} className="w-full max-w-sm space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{t("account.password")}</h2>
            <label className="block text-sm">
              {t("account.current")}
              <input
                type="password"
                required
                value={pw.current}
                onChange={(e) => setPw({ ...pw, current: e.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="block text-sm">
              {t("account.new")} <span className="text-slate-400">{t("account.newHint")}</span>
              <input
                type="password"
                required
                minLength={10}
                value={pw.next}
                onChange={(e) => setPw({ ...pw, next: e.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="block text-sm">
              {t("account.confirm")}
              <input
                type="password"
                required
                minLength={10}
                value={pw.confirm}
                onChange={(e) => setPw({ ...pw, confirm: e.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            {pwErr && <p className="text-sm text-red-600">{pwErr}</p>}
            {pwMsg && <p className="text-sm text-emerald-600">{pwMsg}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setPwOpen(false)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">
                {t("common.close")}
              </button>
              <button disabled={pwBusy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
                {pwBusy ? t("common.saving") : t("common.save")}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
