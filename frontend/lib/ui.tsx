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

interface PromptOptions {
  type?: string;
  placeholder?: string;
  initial?: string;
}

interface PromptState extends PromptOptions {
  text: string;
  value: string;
  resolve: (value: string | null) => void;
}

interface UIContextValue {
  toast: (text: string, type?: ToastType) => void;
  confirm: (text: string) => Promise<boolean>;
  prompt: (text: string, options?: PromptOptions) => Promise<string | null>;
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
  const [promptState, setPromptState] = useState<PromptState | null>(null);
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

  const prompt = useCallback(
    (text: string, options: PromptOptions = {}) =>
      new Promise<string | null>((resolve) =>
        setPromptState({ text, value: options.initial ?? "", resolve, ...options }),
      ),
    [],
  );

  function closeConfirm(ok: boolean) {
    confirmState?.resolve(ok);
    setConfirmState(null);
  }

  function closePrompt(value: string | null) {
    promptState?.resolve(value);
    setPromptState(null);
  }

  return (
    <UIContext.Provider value={{ toast, confirm, prompt }}>
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

      {/* Szöveg-bekérő (prompt) dialógus */}
      {promptState && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) closePrompt(null); }}
          className="fixed inset-0 z-[110] flex items-center justify-center bg-black/40 p-4"
        >
          <form
            onSubmit={(e) => { e.preventDefault(); closePrompt(promptState.value.trim() || null); }}
            className="w-full max-w-sm space-y-4 rounded-2xl bg-white p-6 shadow-xl"
          >
            <label className="block text-sm text-slate-700">
              {promptState.text}
              <input
                autoFocus
                type={promptState.type ?? "text"}
                placeholder={promptState.placeholder}
                value={promptState.value}
                onChange={(e) => setPromptState((s) => (s ? { ...s, value: e.target.value } : s))}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => closePrompt(null)}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100"
              >
                {t("common.cancel")}
              </button>
              <button className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
                {t("common.send")}
              </button>
            </div>
          </form>
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
