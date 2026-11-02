"use client";

/** Offline-segédek (130. kör): a lapok saját localStorage-várólistái (elszámolás,
 *  munkalap, beépítés, feladat-műveletek) egy helyen számolhatók, a keret
 *  (AppShell) globális sávban mutatja a hálózat állapotát és a függő tételeket,
 *  a "Szinkron most" gomb pedig egy közös eseménnyel kéri a lapokat az ürítésre. */

import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";

/** Ismert várólista-kulcsok — minden lap, ami offline sorba tesz, ide regisztrál. */
export const QUEUE_KEYS = [
  "iwfm-pending-settlements", // Elszámolás
  "iwfm-ws-queue", // Munkalap
  "iwfm-parts-queue", // Beépítés
  "iwfm-task-actions", // Feladat: státusz + megjegyzés
] as const;

export const QUEUE_CHANGED_EVENT = "iwfm-queue-changed";
export const FLUSH_EVENT = "iwfm-flush";

export function readQueue<T>(key: string): T[] {
  try {
    return JSON.parse(localStorage.getItem(key) ?? "[]") as T[];
  } catch {
    return [];
  }
}

export function writeQueue<T>(key: string, items: T[]): void {
  try {
    localStorage.setItem(key, JSON.stringify(items));
  } catch {
    /* tárhely-hiba: a hívó a saját állapotát kezeli */
  }
  notifyQueueChanged();
}

/** Összes függő offline tétel az eszközön. */
export function pendingTotal(): number {
  if (typeof window === "undefined") return 0;
  let n = 0;
  for (const key of QUEUE_KEYS) n += readQueue<unknown>(key).length;
  return n;
}

export function notifyQueueChanged(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(QUEUE_CHANGED_EVENT));
}

/** A keret "Szinkron most" gombja: minden lap, ami figyeli, megpróbálja üríteni a sorát. */
export function requestFlush(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(FLUSH_EVENT));
}

/** Hálózati (nem szerver-) hiba: a kérés el sem jutott a backendig → sorba tehető. */
export function isNetworkError(err: unknown): boolean {
  return !(err instanceof ApiError);
}

/** Hálózat-állapot + függő tételek száma, élőben. */
export function useOffline(): { online: boolean; pending: number } {
  const [online, setOnline] = useState(true);
  const [pending, setPending] = useState(0);
  useEffect(() => {
    const sync = () => {
      setOnline(typeof navigator === "undefined" ? true : navigator.onLine);
      setPending(pendingTotal());
    };
    const id = window.setTimeout(sync, 0);
    window.addEventListener("online", sync);
    window.addEventListener("offline", sync);
    window.addEventListener("storage", sync);
    window.addEventListener(QUEUE_CHANGED_EVENT, sync);
    return () => {
      window.clearTimeout(id);
      window.removeEventListener("online", sync);
      window.removeEventListener("offline", sync);
      window.removeEventListener("storage", sync);
      window.removeEventListener(QUEUE_CHANGED_EVENT, sync);
    };
  }, []);
  return { online, pending };
}
