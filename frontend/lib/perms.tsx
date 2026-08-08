"use client";

/** Jogosultság-context: a gyökér-layout PermissionsProvider-e tölti fel a
 *  /api/auth/me permissions listájával (útvonal-váltáskor frissül, így a
 *  belépés/kilépés is érvényesül). A lapok a usePerms() hookkal döntik el,
 *  mit mutatnak — a backend úgyis kikényszeríti a jogokat. */

import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";

export const PermissionsContext = createContext<string[]>([]);

export function PermissionsProvider({ children }: { children: React.ReactNode }) {
  const [perms, setPerms] = useState<string[]>([]);
  const pathname = usePathname();

  useEffect(() => {
    let alive = true;
    api
      .get<{ permissions?: string[] }>("/api/auth/me")
      .then((u) => {
        if (alive) setPerms(u.permissions ?? []);
      })
      .catch(() => {
        if (alive) setPerms([]); // kijelentkezve / publikus oldalon nincs jog
      });
    return () => {
      alive = false;
    };
  }, [pathname]);

  return <PermissionsContext.Provider value={perms}>{children}</PermissionsContext.Provider>;
}

export function usePerms() {
  const perms = useContext(PermissionsContext);
  return {
    perms,
    can: (feature: string) => perms.includes(feature),
  };
}
