"use client";

/** Bejelentkezett keret: fejléc + szerepkör-függő navigáció. */

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { AuthUser } from "@/lib/types";

const NAV = [
  { href: "/vezerlopult", label: "Vezérlőpult", roles: ["admin", "manager"] },
  { href: "/feladatok", label: "Feladatok", roles: ["admin", "manager"] },
  { href: "/dolgozok", label: "Dolgozók", roles: ["admin", "manager"] },
  { href: "/beosztas", label: "Beosztás", roles: ["admin", "manager"] },
  { href: "/tavollet", label: "Távollét", roles: ["admin", "manager"] },
  { href: "/jelenlet", label: "Jelenlét", roles: ["admin", "manager"] },
  { href: "/beallitasok", label: "Beállítások", roles: ["admin"] },
  { href: "/beosztasom", label: "Saját beosztásom", roles: ["employee"] },
  { href: "/feladataim", label: "Feladatok", roles: ["employee"] },
] as const;

export default function AppShell({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const pathname = usePathname();
  const router = useRouter();

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
    return <div className="p-10 text-slate-500">Betöltés…</div>;
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
                {item.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="text-slate-500">{user.display_name}</span>
            <button
              onClick={logout}
              className="rounded-lg border border-slate-300 px-3 py-1.5 text-slate-600 hover:bg-slate-100"
            >
              Kijelentkezés
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
    </div>
  );
}
