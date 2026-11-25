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
  "iwfm-intake-queue", // Átvétel (181. kör) — a fotók IndexedDB-ben
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

/** IndexedDB kulcs-érték tár a nagy tételeknek (fotók): a localStorage ~5 MB-ja
 *  ehhez kevés. Hiba esetén a hívó dönt (pl. fotók nélkül sorba teszi). */
const IDB_NAME = "iwfm-offline";
const IDB_STORE = "blobs";

function openIdb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") { reject(new Error("no-idb")); return; }
    const req = indexedDB.open(IDB_NAME, 1);
    req.onupgradeneeded = () => { req.result.createObjectStore(IDB_STORE); };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("idb"));
  });
}

function idbTx<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return openIdb().then((db) => new Promise<T>((resolve, reject) => {
    const tx = db.transaction(IDB_STORE, mode);
    const req = run(tx.objectStore(IDB_STORE));
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("idb"));
    tx.oncomplete = () => db.close();
  }));
}

export const idbPut = (key: string, value: unknown) => idbTx("readwrite", (s) => s.put(value, key));
export const idbGet = <T>(key: string) => idbTx<T | undefined>("readonly", (s) => s.get(key) as IDBRequest<T | undefined>);
export const idbDel = (key: string) => idbTx("readwrite", (s) => s.delete(key));

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
