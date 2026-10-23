"use client";

/** Jogosultság-mátrix (RBAC v2): szerepkörök (beépített + saját) × funkciók,
 *  funkciónként a négy művelet (megtekintés / létrehozás / módosítás / törlés)
 *  külön kapcsolható; minden funkcióhoz és művelethez részletes buborék-
 *  magyarázat; saját szerepkör létrehozása, átnevezése, törlése. Az admin
 *  mindig mindent elér, ő nem szerkeszthető. */

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface RoleRow { key: string; name: string; description: string | null; level: number; is_system: boolean; users: number }
interface Payload {
  roles: RoleRow[];
  admin: RoleRow | null;
  features: string[];
  actions_catalog: string[];
  matrix: Record<string, string[]>;
  actions: Record<string, Record<string, string[]>>;
}

const ACTIONS = ["view", "create", "edit", "delete"] as const;
const ACTION_ICON: Record<string, string> = { view: "👁", create: "➕", edit: "✏️", delete: "🗑" };

/** Buborék: hover/fókusz esetén a részletes magyarázat. */
export function Tip({ text, children, wide = false }: { text: string; children: ReactNode; wide?: boolean }) {
  return (
    <span className="group relative inline-flex items-center">
      {children}
      <span
        role="tooltip"
        className={`pointer-events-none absolute left-0 top-full z-[80] mt-1 hidden whitespace-pre-line rounded-xl border border-slate-200 bg-white p-3 text-left text-xs font-normal leading-relaxed text-slate-700 shadow-xl group-hover:block group-focus-within:block ${wide ? "w-[28rem] max-w-[90vw]" : "w-72"}`}
      >
        {text}
      </span>
    </span>
  );
}

export default function PermissionMatrix() {
  const { t } = useT();
  const { toast, confirm } = useUI();
  const [data, setData] = useState<Payload | null>(null);
  const [matrix, setMatrix] = useState<Record<string, string[]>>({});
  const [actions, setActions] = useState<Record<string, Record<string, string[]>>>({});
  const [open, setOpen] = useState<Set<string>>(new Set()); // "role|feature" nyitott művelet-sorok
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [roleForm, setRoleForm] = useState<{ key: string; name: string; description: string; level: 0 | 1; copy_from: string; editKey?: string } | null>(null);

  const load = useCallback(() => {
    api.get<Payload>("/api/settings/permissions").then((p) => {
      setData(p);
      setMatrix(p.matrix);
      setActions(p.actions);
      setDirty(false);
    }).catch((e) => toast(errorMessage(e), "error"));
  }, [toast]);
  useEffect(load, [load]);

  if (!data) return <p className="text-sm text-slate-400">{t("common.loading")}</p>;

  const has = (role: string, f: string) => (matrix[role] ?? []).includes(f);
  const allowedActs = (role: string, f: string) => actions[role]?.[f] ?? [...ACTIONS];
  const actOn = (role: string, f: string, a: string) => allowedActs(role, f).includes(a);

  function toggleFeature(role: string, f: string) {
    setMatrix((m) => {
      const cur = m[role] ?? [];
      return { ...m, [role]: cur.includes(f) ? cur.filter((x) => x !== f) : [...cur, f] };
    });
    setDirty(true);
  }
  function toggleAction(role: string, f: string, a: string) {
    setActions((all) => {
      const cur = all[role]?.[f] ?? [...ACTIONS];
      const next = cur.includes(a) ? cur.filter((x) => x !== a) : [...cur, a];
      const roleMap = { ...(all[role] ?? {}) };
      if (next.length === ACTIONS.length) delete roleMap[f]; else roleMap[f] = ACTIONS.filter((x) => next.includes(x));
      return { ...all, [role]: roleMap };
    });
    setDirty(true);
  }
  function toggleOpen(role: string, f: string) {
    setOpen((s) => { const n = new Set(s); const k = `${role}|${f}`; if (n.has(k)) n.delete(k); else n.add(k); return n; });
  }

  async function save() {
    setBusy(true);
    try {
      const p = await api.put<Payload>("/api/settings/permissions", { matrix, actions });
      setData(p); setMatrix(p.matrix); setActions(p.actions); setDirty(false);
      toast(t("perms.saved"), "success");
    } catch (e) {
      toast(errorMessage(e), "error");
    } finally {
      setBusy(false);
    }
  }

  async function saveRole() {
    if (!roleForm) return;
    setBusy(true);
    try {
      if (roleForm.editKey) {
        await api.patch(`/api/settings/roles/${roleForm.editKey}`, { name: roleForm.name, description: roleForm.description, level: roleForm.level });
      } else {
        await api.post("/api/settings/roles", { key: roleForm.key.trim().toLowerCase(), name: roleForm.name, description: roleForm.description, level: roleForm.level, copy_from: roleForm.copy_from || null });
      }
      setRoleForm(null);
      load();
      toast(t("perms.roleSaved"), "success");
    } catch (e) {
      toast(errorMessage(e), "error");
    } finally {
      setBusy(false);
    }
  }

  async function deleteRole(r: RoleRow) {
    if (!(await confirm(t("perms.roleDeleteConfirm", { name: r.name })))) return;
    try {
      await api.delete(`/api/settings/roles/${r.key}`);
      load();
      toast(t("perms.roleDeleted"), "success");
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  }

  const roleLabel = (r: RoleRow) => r.name;
  const featureDesc = (f: string) => t(`perms.desc.${f}`);

  return (
    <div>
      <div className="flex flex-wrap items-start gap-3">
        <div>
          <h2 className="font-semibold">🔐 {t("settings.permTitle")}</h2>
          <p className="text-xs text-slate-500">{t("perms.hint")}</p>
        </div>
        <button
          onClick={() => setRoleForm({ key: "", name: "", description: "", level: 0, copy_from: "" })}
          className="ml-auto rounded-lg border border-indigo-300 px-3 py-1.5 text-sm text-indigo-700 hover:bg-indigo-50"
        >
          ＋ {t("perms.newRole")}
        </button>
      </div>

      {/* Jelmagyarázat */}
      <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-500">
        {ACTIONS.map((a) => (
          <Tip key={a} text={t(`perms.actions.${a}.desc`)}>
            <span className="cursor-help rounded bg-slate-100 px-2 py-0.5">{ACTION_ICON[a]} {t(`perms.actions.${a}.label`)}</span>
          </Tip>
        ))}
        <Tip text={t("perms.levelsDesc")} wide>
          <span className="cursor-help rounded bg-slate-100 px-2 py-0.5">🏷 {t("perms.levelsTitle")}</span>
        </Tip>
      </div>

      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
              <th className="px-3 py-2">{t("settings.permFeature")}</th>
              <th className="px-3 py-2 text-center">
                <Tip text={t("perms.adminDesc")}><span className="cursor-help">{data.admin?.name ?? "Admin"}</span></Tip>
              </th>
              {data.roles.map((r) => (
                <th key={r.key} className="px-3 py-2 text-center align-top">
                  <Tip text={`${r.description ?? ""}\n\n${t("perms.roleMeta", { level: t(`perms.levels.${r.level === 1 ? "manager" : "employee"}`), users: r.users })}`} wide>
                    <span className="cursor-help">{roleLabel(r)}{!r.is_system && <span className="ml-1 rounded bg-indigo-50 px-1 text-[10px] text-indigo-700">{t("perms.custom")}</span>}</span>
                  </Tip>
                  <div className="mt-1 flex justify-center gap-1 text-[11px] normal-case">
                    <button type="button" onClick={() => setRoleForm({ key: r.key, name: r.name, description: r.description ?? "", level: (r.level === 1 ? 1 : 0), copy_from: "", editKey: r.key })} className="rounded px-1 text-slate-500 hover:bg-slate-100" title={t("common.edit")}>✏️</button>
                    {!r.is_system && <button type="button" onClick={() => void deleteRole(r)} className="rounded px-1 text-rose-600 hover:bg-rose-50" title={t("common.delete")}>🗑</button>}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.features.map((f) => (
              <tr key={f} className="border-b border-slate-100 align-top last:border-0">
                <td className="px-3 py-2">
                  <Tip text={featureDesc(f)} wide>
                    <span className="cursor-help border-b border-dotted border-slate-400">{t(`features.${f}`)}</span>
                  </Tip>
                  {f === "delete" && <span className="ml-2 rounded bg-red-50 px-1.5 py-0.5 text-xs text-red-700">{t("settings.permDeleteHint")}</span>}
                </td>
                <td className="px-3 py-2 text-center"><input type="checkbox" checked disabled className="h-4 w-4 opacity-50" /></td>
                {data.roles.map((r) => {
                  const on = has(r.key, f);
                  const acts = allowedActs(r.key, f);
                  const restricted = on && acts.length < ACTIONS.length;
                  const isOpen = open.has(`${r.key}|${f}`);
                  return (
                    <td key={r.key} className="px-3 py-2 text-center">
                      <div className="inline-flex items-center gap-1">
                        <input type="checkbox" checked={on} onChange={() => toggleFeature(r.key, f)} className="h-4 w-4" title={t("perms.toggleFeature")} />
                        {on && (
                          <button
                            type="button"
                            onClick={() => toggleOpen(r.key, f)}
                            title={t("perms.openActions")}
                            className={`rounded px-1 text-[11px] ${restricted ? "bg-amber-50 text-amber-700" : "text-slate-400 hover:bg-slate-100"}`}
                          >
                            {restricted ? `⚙ ${acts.length}/4` : "⚙"}
                          </button>
                        )}
                      </div>
                      {on && isOpen && (
                        <div className="mt-1 flex justify-center gap-1">
                          {ACTIONS.map((a) => (
                            <Tip key={a} text={`${t(`perms.actions.${a}.label`)}: ${t(`perms.actions.${a}.desc`)}`}>
                              <button
                                type="button"
                                onClick={() => toggleAction(r.key, f, a)}
                                className={`rounded px-1.5 py-0.5 text-[11px] ring-1 ${actOn(r.key, f, a) ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : "bg-rose-50 text-rose-600 line-through ring-rose-200"}`}
                              >
                                {ACTION_ICON[a]}
                              </button>
                            </Tip>
                          ))}
                        </div>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex items-center gap-3">
        <button onClick={() => void save()} disabled={busy || !dirty} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
          {busy ? t("common.saving") : t("common.save")}
        </button>
        {dirty && <span className="text-xs text-amber-700">{t("perms.unsaved")}</span>}
      </div>

      {roleForm && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={(e) => { e.preventDefault(); void saveRole(); }} className="relative w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{roleForm.editKey ? t("perms.editRole") : t("perms.newRole")}</h2>
            <p className="text-xs text-slate-500">{t("perms.roleFormHint")}</p>
            {!roleForm.editKey && (
              <label className="block text-sm">
                {t("perms.roleKey")} *
                <input required pattern="[a-z][a-z0-9_]{1,31}" value={roleForm.key} onChange={(e) => setRoleForm({ ...roleForm, key: e.target.value })} placeholder="auditor" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 font-mono" />
                <span className="text-xs text-slate-400">{t("perms.roleKeyHint")}</span>
              </label>
            )}
            <label className="block text-sm">
              {t("perms.roleName")} *
              <input required value={roleForm.name} onChange={(e) => setRoleForm({ ...roleForm, name: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              {t("perms.roleDesc")}
              <textarea rows={3} value={roleForm.description} onChange={(e) => setRoleForm({ ...roleForm, description: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" placeholder={t("perms.roleDescPh")} />
            </label>
            {(!roleForm.editKey || !data.roles.find((r) => r.key === roleForm.editKey)?.is_system) && (
              <label className="block text-sm">
                {t("perms.roleLevel")}
                <select value={roleForm.level} onChange={(e) => setRoleForm({ ...roleForm, level: Number(e.target.value) === 1 ? 1 : 0 })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2">
                  <option value={0}>{t("perms.levels.employee")}</option>
                  <option value={1}>{t("perms.levels.manager")}</option>
                </select>
                <span className="text-xs text-slate-400">{t("perms.levelsDesc")}</span>
              </label>
            )}
            {!roleForm.editKey && (
              <label className="block text-sm">
                {t("perms.copyFrom")}
                <select value={roleForm.copy_from} onChange={(e) => setRoleForm({ ...roleForm, copy_from: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2">
                  <option value="">{t("perms.copyNone")}</option>
                  <option value="admin">{data.admin?.name ?? "Admin"}</option>
                  {data.roles.map((r) => <option key={r.key} value={r.key}>{r.name}</option>)}
                </select>
              </label>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setRoleForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{busy ? t("common.saving") : t("common.save")}</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
