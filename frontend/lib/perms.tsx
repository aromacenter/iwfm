"use client";

/** Jogosultság-context: az AppShell tölti fel a bejelentkezett user
 *  /api/auth/me-ből kapott permissions listájával; a lapok a usePerms()
 *  hookkal döntik el, mit mutatnak (a backend úgyis kikényszeríti). */

import { createContext, useContext } from "react";

export const PermissionsContext = createContext<string[]>([]);

export function usePerms() {
  const perms = useContext(PermissionsContext);
  return {
    perms,
    can: (feature: string) => perms.includes(feature),
  };
}
