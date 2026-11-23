"use client";

/** 170. kör: a Flotta-pult (X-systems üzemeltető) fiók-kérései — csak adminnak.
 * Percenként lekérdezi a nyitott tételeket, és felugró ablakban mutatja:
 *  - új fiók kérése: Elfogadom / Elutasítom (indokkal) — a fiók CSAK az
 *    elfogadás után jön létre, és a 24 órás jelszó-beállító link itt jelenik meg;
 *  - a pultból megszüntetett fiókról szóló értesítő: Rendben (nyugtázás). */

import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

type Item = {
  id: string;
  kind: "create" | "delete";
  status: string;
  display_name: string;
  login: string | null;
  role: string;
  with_employee: boolean;
  is_operator: boolean;
  note: string | null;
  requested_at: string | null;
};

const POLL_MS = 60_000;

export default function OperatorRequests() {
  const { t } = useT();
  const { toast } = useUI();
  const [items, setItems] = useState<Item[]>([]);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const [declining, setDeclining] = useState(false);
  const [resetUrl, setResetUrl] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .get<{ items: Item[] }>("/api/user-requests")
      .then((r) => setItems(r.items))
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS);
    const onFocus = () => load();
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, [load]);

  const cur = items[0];
  if (!cur && !resetUrl) return null;

  async function approve() {
    if (!cur) return;
    setBusy(true);
    try {
      const r = await api.post<{ reset_url: string }>(`/api/user-requests/${cur.id}/approve`, {});
      setResetUrl(r.reset_url);
      toast(t("opReq.approved"), "success");
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function decline() {
    if (!cur) return;
    setBusy(true);
    try {
      await api.post(`/api/user-requests/${cur.id}/decline`, { reason: reason.trim() || null });
      toast(t("opReq.declined"), "success");
      setDeclining(false);
      setReason("");
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function ack() {
    if (!cur) return;
    setBusy(true);
    try {
      await api.post(`/api/user-requests/${cur.id}/ack`, {});
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function copyLink() {
    if (!resetUrl) return;
    try {
      await navigator.clipboard.writeText(resetUrl);
      toast(t("opReq.linkCopied"), "success");
    } catch {
      toast(t("opReq.copyFailed"), "error");
    }
  }

  const roleLabel = (k: string) => t(`roles.${k}`) === `roles.${k}` ? k : t(`roles.${k}`);

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
        {resetUrl ? (
          <>
            <h2 className="text-lg font-semibold">✅ {t("opReq.doneTitle")}</h2>
            <p className="text-sm text-slate-600">{t("opReq.doneHint")}</p>
            <input readOnly value={resetUrl} onClick={(e) => (e.target as HTMLInputElement).select()} className="w-full rounded-lg border border-slate-300 bg-slate-50 px-2 py-1 font-mono text-xs" />
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => void copyLink()} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50">
                📋 {t("opReq.copyLink")}
              </button>
              <button type="button" onClick={() => setResetUrl(null)} className="rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700">
                {t("common.close")}
              </button>
            </div>
          </>
        ) : cur && cur.kind === "delete" ? (
          <>
            <h2 className="text-lg font-semibold">🗑 {t("opReq.deleteTitle")}</h2>
            <p className="text-sm text-slate-700">
              {t("opReq.deleteBody", { name: cur.display_name, login: cur.login ?? "" })}
            </p>
            <div className="flex justify-end">
              <button type="button" disabled={busy} onClick={() => void ack()} className="rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50">
                {t("opReq.ack")}
              </button>
            </div>
          </>
        ) : cur ? (
          <>
            <h2 className="text-lg font-semibold">🛠 {t("opReq.title")}</h2>
            <p className="text-sm text-slate-600">{t("opReq.intro")}</p>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-xl bg-slate-50 p-3 text-sm">
              <dt className="text-slate-500">{t("opReq.name")}</dt><dd className="font-medium">{cur.display_name}</dd>
              <dt className="text-slate-500">{t("opReq.login")}</dt><dd className="font-mono text-xs">{cur.login}</dd>
              <dt className="text-slate-500">{t("opReq.role")}</dt>
              <dd>{cur.is_operator ? t("opReq.operatorRole") : roleLabel(cur.role)}</dd>
              <dt className="text-slate-500">{t("opReq.card")}</dt><dd>{cur.with_employee ? t("common.yes") : t("common.no")}</dd>
              {cur.note && (<><dt className="text-slate-500">{t("opReq.note")}</dt><dd className="whitespace-pre-wrap">{cur.note}</dd></>)}
            </dl>
            {cur.is_operator && (
              <p className="rounded-lg border border-rose-200 bg-rose-50 p-2 text-xs text-rose-800">{t("opReq.operatorWarn")}</p>
            )}
            {declining && (
              <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={256} placeholder={t("opReq.reasonPlaceholder")} className="w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm" />
            )}
            <div className="flex flex-wrap justify-end gap-2">
              {declining ? (
                <>
                  <button type="button" disabled={busy} onClick={() => setDeclining(false)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50">{t("common.cancel")}</button>
                  <button type="button" disabled={busy} onClick={() => void decline()} className="rounded-lg bg-rose-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-rose-700 disabled:opacity-50">{t("opReq.declineConfirm")}</button>
                </>
              ) : (
                <>
                  <button type="button" disabled={busy} onClick={() => setDeclining(true)} className="rounded-lg border border-rose-300 px-3 py-1.5 text-sm text-rose-700 hover:bg-rose-50">{t("opReq.decline")}</button>
                  <button type="button" disabled={busy} onClick={() => void approve()} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50">{t("opReq.approve")}</button>
                </>
              )}
            </div>
            {items.length > 1 && <p className="text-xs text-slate-400">{t("opReq.more", { n: items.length - 1 })}</p>}
          </>
        ) : null}
      </div>
    </div>
  );
}
