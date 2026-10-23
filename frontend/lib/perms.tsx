"use client";

/** Jogosultság-context: a gyökér-layout PermissionsProvider-e tölti fel a
 *  /api/auth/me permissions + denied_actions listájával (útvonal-váltáskor
 *  frissül, így a belépés/kilépés is érvényesül). A lapok a usePerms() hookkal
 *  döntik el, mit mutatnak — a backend úgyis kikényszeríti a jogokat.
 *  RBAC v2: can(feature) a funkció, can(feature, "edit") a művelet
 *  (view | create | edit | delete) engedélyezettségét adja. */

import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";

export type PermAction = "view" | "create" | "edit" | "delete";

export interface PermState {
  perms: string[];
  denied: string[]; // "feature.action"
  role: string | null;
  roleName: string | null;
  level: number;
  loaded: boolean;
}

const EMPTY: PermState = { perms: [], denied: [], role: null, roleName: null, level: 0, loaded: false };

export const PermissionsContext = createContext<PermState>(EMPTY);

export function PermissionsProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<PermState>(EMPTY);
  const pathname = usePathname();

  useEffect(() => {
    let alive = true;
    api
      .get<{ permissions?: string[]; denied_actions?: string[]; role?: string; role_name?: string | null; level?: number }>("/api/auth/me")
      .then((u) => {
        if (alive) setState({ perms: u.permissions ?? [], denied: u.denied_actions ?? [], role: u.role ?? null, roleName: u.role_name ?? null, level: u.level ?? 0, loaded: true });
      })
      .catch(() => {
        if (alive) setState({ ...EMPTY, loaded: true }); // kijelentkezve / publikus oldalon nincs jog
      });
    return () => {
      alive = false;
    };
  }, [pathname]);

  return <PermissionsContext.Provider value={state}>{children}</PermissionsContext.Provider>;
}

export function usePerms() {
  const s = useContext(PermissionsContext);
  return {
    perms: s.perms,
    denied: s.denied,
    role: s.role,
    roleName: s.roleName,
    level: s.level,
    loaded: s.loaded,
    isManager: s.role === "admin" || s.level >= 1,
    can: (feature: string, action?: PermAction) =>
      s.perms.includes(feature) && (!action || !s.denied.includes(`${feature}.${action}`)),
  };
}
