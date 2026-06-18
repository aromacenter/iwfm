"use client";

/** Globális UI-segédek: toast üzenetek + megerősítő (confirm) dialógus.
 *  A natív alert()/confirm() helyett — mobilbarát, a felület stílusához illik.
 *
 *  Használat egy komponensben:
 *    const { toast, confirm } = useUI();
 *    toast(t("..."), "error");
 *    if (!(await confirm(t("...")))) return;
 */

import { createContext, useCallback, useContext, useRef, useState } from "react";
import { useT } from "@/lib/i18n";

type ToastType = "info" | "success" | "error";

interface ToastItem {
  id: number;
  text: string;
  type: ToastType;
}

interface ConfirmState {
  text: string;
  resolve: (ok: boolean) => void;
}

interface UIContextValue {
  toast: (text: string, type?: ToastType) => void;
  confirm: (text: string) => Promise<boolean>;
}

const UIContext = createContext<UIContextValue | null>(null);

const TOAST_STYLES: Record<ToastType, string> = {
  info: "bg-slate-800 text-white",
  success: "bg-emerald-600 text-white",
  error: "bg-red-600 text-white",
};

export function UIProvider({ children }: { children: React.ReactNode }) {
  const { t } = useT();
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const nextId = useRef(1);

  const toast = useCallback((text: string, type: ToastType = "info") => {
    const id = nextId.current++;
    setToasts((list) => [...list, { id, text, type }]);
    setTimeout(() => setToasts((list) => list.filter((x) => x.id !== id)), 4500);
  }, []);

  const confirm = useCallback(
    (text: string) => new Promise<boolean>((resolve) => setConfirmState({ text, resolve })),
    [],
  );

  function closeConfirm(ok: boolean) {
    confirmState?.resolve(ok);
    setConfirmState(null);
  }

  return (
    <UIContext.Provider value={{ toast, confirm }}>
      {children}

      {/* Toast-verem — mobilon alul középen, nagyobb képernyőn jobbra lent */}
      <div className="pointer-events-none fixed inset-x-0 bottom-0 z-[100] flex flex-col items-center gap-2 p-4 sm:inset-x-auto sm:right-0 sm:items-end">
        {toasts.map((item) => (
          <div
            key={item.id}
            role="status"
            className={`pointer-events-auto max-w-sm rounded-xl px-4 py-2.5 text-sm shadow-lg ${TOAST_STYLES[item.type]}`}
          >
            {item.text}
          </div>
        ))}
      </div>

      {/* Megerősítő dialógus */}
      {confirmState && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) closeConfirm(false); }}
          className="fixed inset-0 z-[110] flex items-center justify-center bg-black/40 p-4"
        >
          <div className="w-full max-w-sm space-y-4 rounded-2xl bg-white p-6 shadow-xl">
            <p className="whitespace-pre-line text-sm text-slate-700">{confirmState.text}</p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => closeConfirm(false)}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100"
              >
                {t("common.cancel")}
              </button>
              <button
                onClick={() => closeConfirm(true)}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
              >
                {t("common.yes")}
              </button>
            </div>
          </div>
        </div>
      )}
    </UIContext.Provider>
  );
}

export function useUI(): UIContextValue {
  const ctx = useContext(UIContext);
  if (!ctx) throw new Error("useUI must be used within UIProvider");
  return ctx;
}
