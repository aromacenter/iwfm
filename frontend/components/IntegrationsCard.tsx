"use client";

/** Integrációk (187. kör): kimenő webhookok (URL, események, titok, próba,
 *  számlálók) és olvasó API-kulcsok (egyszer látható kulcs, visszavonás).
 *  Csak admin; a Beállítások „Integrációk” fülén. */

import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Webhook {
  id: string; name: string; url: string; events: string[]; active: boolean; secret?: string | null; secret_hint: string;
  delivered: number; failures: number; last_status: number | null; last_at: string | null; last_error: string | null; created_at: string;
}
interface ApiKey { id: string; name: string; prefix: string; key?: string | null; created_by_name: string; created_at: string; last_used_at: string | null; revoked_at: string | null }
interface EventDef { event: string; fields: string[] }

const inputCls = "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm";
const fmt = (s: string | null) => (s ? s.slice(0, 16).replace("T", " ") : "");

function CopyBox({ value, label, copy, copied }: { value: string; label: string; copy: string; copied: string }) {
  const { toast } = useUI();
  return (
    <div className="mt-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
      <div className="font-semibold">{label}</div>
      <div className="mt-1 flex items-center gap-2">
        <code className="flex-1 break-all rounded bg-white px-2 py-1 font-mono text-xs">{value}</code>
        <button type="button" onClick={() => { navigator.clipboard?.writeText(value).then(() => toast(copied, "success")).catch(() => {}); }} className="rounded-lg border border-amber-400 px-2 py-1 text-xs">{copy}</button>
      </div>
    </div>
  );
}

export default function IntegrationsCard() {
  const { t } = useT();
  const { toast, confirm } = useUI();
  const [hooks, setHooks] = useState<Webhook[]>([]);
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [events, setEvents] = useState<EventDef[]>([]);
  const [whName, setWhName] = useState("");
  const [whUrl, setWhUrl] = useState("");
  const [whEvents, setWhEvents] = useState<Set<string>>(new Set());
  const [newSecret, setNewSecret] = useState<{ id: string; secret: string } | null>(null);
  const [keyName, setKeyName] = useState("");
  const [newKey, setNewKey] = useState<{ id: string; key: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get<Webhook[]>("/api/integrations/webhooks").then(setHooks).catch(() => {});
    api.get<ApiKey[]>("/api/integrations/api-keys").then(setKeys).catch(() => {});
    api.get<EventDef[]>("/api/integrations/events").then(setEvents).catch(() => {});
  }, []);
  useEffect(load, [load]);

  async function addHook() {
    if (!whName.trim() || !/^https?:\/\//.test(whUrl.trim())) return;
    setBusy(true);
    try {
      const w = await api.post<Webhook>("/api/integrations/webhooks", { name: whName.trim(), url: whUrl.trim(), events: Array.from(whEvents), active: true });
      setNewSecret(w.secret ? { id: w.id, secret: w.secret } : null);
      setWhName(""); setWhUrl(""); setWhEvents(new Set());
      load();
    } catch (e) { toast(errorMessage(e), "error"); } finally { setBusy(false); }
  }
  async function testHook(w: Webhook) {
    try {
      const r = await api.post<{ ok: boolean; status: number; error: string | null }>(`/api/integrations/webhooks/${w.id}/test`);
      toast(r.ok ? t("integr.whTestOk", { status: r.status }) : t("integr.whTestFail", { error: r.error ?? `HTTP ${r.status}` }), r.ok ? "success" : "error");
      load();
    } catch (e) { toast(errorMessage(e), "error"); }
  }
  async function toggleHook(w: Webhook) {
    try { await api.patch(`/api/integrations/webhooks/${w.id}`, { active: !w.active }); load(); } catch (e) { toast(errorMessage(e), "error"); }
  }
  async function rotate(w: Webhook) {
    if (!(await confirm(t("integr.whRotateConfirm")))) return;
    try {
      const r = await api.patch<Webhook>(`/api/integrations/webhooks/${w.id}`, { rotate_secret: true });
      setNewSecret(r.secret ? { id: r.id, secret: r.secret } : null);
      load();
    } catch (e) { toast(errorMessage(e), "error"); }
  }
  async function delHook(w: Webhook) {
    if (!(await confirm(t("integr.whDeleteConfirm", { name: w.name })))) return;
    try { await api.delete(`/api/integrations/webhooks/${w.id}`); load(); } catch (e) { toast(errorMessage(e), "error"); }
  }
  async function addKey() {
    if (!keyName.trim()) return;
    setBusy(true);
    try {
      const k = await api.post<ApiKey>("/api/integrations/api-keys", { name: keyName.trim() });
      setNewKey(k.key ? { id: k.id, key: k.key } : null);
      setKeyName("");
      load();
    } catch (e) { toast(errorMessage(e), "error"); } finally { setBusy(false); }
  }
  async function revoke(k: ApiKey) {
    if (!(await confirm(t("integr.keyRevokeConfirm", { name: k.name })))) return;
    try { await api.delete(`/api/integrations/api-keys/${k.id}`); load(); } catch (e) { toast(errorMessage(e), "error"); }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="font-semibold">🔌 {t("integr.title")}</h2>
        <p className="mt-1 text-sm text-slate-500">{t("integr.hint")}</p>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 className="font-semibold">{t("integr.whTitle")}</h3>
        {hooks.length === 0 && <p className="mt-2 text-sm text-slate-400">{t("integr.whEmpty")}</p>}
        <div className="mt-2 space-y-2">
          {hooks.map((w) => (
            <div key={w.id} className="rounded-xl border border-slate-200 p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">{w.name}</span>
                <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${w.active ? "bg-emerald-100 text-emerald-800" : "bg-slate-200 text-slate-600"}`}>{w.active ? t("integr.whActive") : t("integr.whInactive")}</span>
                <code className="break-all text-xs text-slate-500">{w.url}</code>
              </div>
              <div className="mt-1 text-xs text-slate-500">
                {w.events.length ? w.events.join(", ") : t("integr.whAllEvents")} · {t("integr.whStats", { n: w.delivered, f: w.failures })}
                {w.last_status != null && <> · {t("integr.whLast", { status: w.last_status, at: fmt(w.last_at) })}</>}
                {w.last_error && <span className="text-red-600"> · {w.last_error}</span>}
                <span className="ml-1 font-mono">({w.secret_hint})</span>
              </div>
              {newSecret?.id === w.id && <CopyBox value={newSecret.secret} label={t("integr.whSecretOnce")} copy={t("integr.copy")} copied={t("integr.copied")} />}
              <div className="mt-2 flex flex-wrap gap-2">
                <button type="button" onClick={() => testHook(w)} className="rounded-lg border border-indigo-300 px-3 py-1 text-xs text-indigo-700">{t("integr.whTest")}</button>
                <button type="button" onClick={() => toggleHook(w)} className="rounded-lg border border-slate-300 px-3 py-1 text-xs">{w.active ? "⏸" : "▶"}</button>
                <button type="button" onClick={() => rotate(w)} className="rounded-lg border border-slate-300 px-3 py-1 text-xs">{t("integr.whRotate")}</button>
                <button type="button" onClick={() => delHook(w)} className="rounded-lg border border-red-300 px-3 py-1 text-xs text-red-700">{t("integr.whDelete")}</button>
              </div>
            </div>
          ))}
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <label className="block text-sm">{t("integr.whName")}<input value={whName} onChange={(e) => setWhName(e.target.value)} className={inputCls} /></label>
          <label className="block text-sm">{t("integr.whUrl")}<input value={whUrl} onChange={(e) => setWhUrl(e.target.value)} placeholder="https://" className={inputCls} /></label>
        </div>
        <div className="mt-2 text-sm">
          <div className="text-xs font-semibold uppercase text-slate-500">{t("integr.whEvents")}</div>
          <div className="mt-1 grid max-h-40 gap-1 overflow-auto rounded-lg border border-slate-200 p-2 sm:grid-cols-2 lg:grid-cols-3">
            {events.map((ev) => (
              <label key={ev.event} className="flex items-center gap-2 text-xs">
                <input type="checkbox" checked={whEvents.has(ev.event)} onChange={(e) => { const n = new Set(whEvents); if (e.target.checked) n.add(ev.event); else n.delete(ev.event); setWhEvents(n); }} />
                <span className="font-mono">{ev.event}</span>
              </label>
            ))}
          </div>
        </div>
        <button type="button" disabled={busy || !whName.trim() || !/^https?:\/\//.test(whUrl.trim())} onClick={addHook} className="mt-3 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">{t("integr.whAdd")}</button>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 className="font-semibold">{t("integr.keyTitle")}</h3>
        {keys.length === 0 && <p className="mt-2 text-sm text-slate-400">{t("integr.keyEmpty")}</p>}
        <div className="mt-2 space-y-2">
          {keys.map((k) => (
            <div key={k.id} className={`rounded-xl border border-slate-200 p-3 text-sm ${k.revoked_at ? "opacity-60" : ""}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">{k.name}</span>
                <code className="font-mono text-xs text-slate-500">{k.prefix}…</code>
                {k.revoked_at && <span className="rounded-full bg-slate-200 px-2 py-0.5 text-xs">{t("integr.keyRevoked")}</span>}
                <span className="text-xs text-slate-400">{k.created_by_name} · {fmt(k.created_at)} · {k.last_used_at ? t("integr.keyLastUsed", { at: fmt(k.last_used_at) }) : t("integr.keyNever")}</span>
                {!k.revoked_at && <button type="button" onClick={() => revoke(k)} className="ml-auto rounded-lg border border-red-300 px-3 py-1 text-xs text-red-700">{t("integr.keyRevoke")}</button>}
              </div>
              {newKey?.id === k.id && <CopyBox value={newKey.key} label={t("integr.keyOnce")} copy={t("integr.copy")} copied={t("integr.copied")} />}
            </div>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="block flex-1 text-sm">{t("integr.keyName")}<input value={keyName} onChange={(e) => setKeyName(e.target.value)} className={inputCls} /></label>
          <button type="button" disabled={busy || !keyName.trim()} onClick={addKey} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">{t("integr.keyAdd")}</button>
        </div>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-slate-50 p-5 text-sm text-slate-700">
        <h3 className="font-semibold">{t("integr.docsTitle")}</h3>
        <p className="mt-2">{t("integr.docsWebhook")}</p>
        <pre className="mt-2 overflow-auto rounded-lg bg-white p-3 text-xs">{`POST <url>
X-Iwfm-Event: settlement.created
X-Iwfm-Signature: sha256=<hmac_sha256(secret, body)>

{"event":"settlement.created","at":"2026-11-26T10:00:00Z",
 "data":{"partner_nev":"Pláza Kávézó","vegosszeg_brutto":48200},
 "ids":{"settlement_id":"…","partner_id":"…"},
 "link":"https://…/elszamolas?settlement=…"}`}</pre>
        <p className="mt-3">{t("integr.docsKey")}</p>
        <pre className="mt-2 overflow-auto rounded-lg bg-white p-3 text-xs">{`curl -H "X-Api-Key: xa_…" https://<példány>/api/partners`}</pre>
      </div>
    </div>
  );
}
