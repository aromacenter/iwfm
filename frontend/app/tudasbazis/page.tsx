"use client";

/** Tudásbázis: kézzel rögzített, bevált megoldások gépenként (házi tudás,
 *  ami nincs benne a kézikönyvekben). A QR-oldali AI chat ezekből is
 *  válaszol, a gép típusára szűrve. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import IconLegend from "@/components/IconLegend";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { usePerms } from "@/lib/perms";
import { useUI } from "@/lib/ui";

interface Entry {
  id: string;
  machine_label: string;
  title: string;
  content: string;
  source: string;
  created_by_name: string | null;
  created_at: string;
  updated_at: string;
}

interface FormState {
  id: string | null;
  machine_label: string;
  title: string;
  content: string;
}

const EMPTY_FORM: FormState = { id: null, machine_label: "", title: "", content: "" };

export default function TudasbazisPage() {
  const { t, lang } = useT();
  const { toast, confirm } = useUI();
  const canDelete = usePerms().can("delete");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [machines, setMachines] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [machineFilter, setMachineFilter] = useState("");
  const [form, setForm] = useState<FormState | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fmt = (dt: string) =>
    new Date(dt).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "short", timeStyle: "short" });

  const load = useCallback(() => {
    const params = new URLSearchParams();
    if (search.trim()) params.set("q", search.trim());
    if (machineFilter) params.set("machine", machineFilter);
    const qs = params.toString();
    api.get<Entry[]>(`/api/knowledge${qs ? `?${qs}` : ""}`).then(setEntries).catch(() => {});
  }, [search, machineFilter]);

  useEffect(load, [load]);
  useEffect(() => {
    api.get<string[]>("/api/knowledge/machines").then(setMachines).catch(() => {});
  }, []);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!form) return;
    setBusy(true);
    setError(null);
    try {
      const body = {
        machine_label: form.machine_label,
        title: form.title,
        content: form.content,
      };
      if (form.id) await api.patch(`/api/knowledge/${form.id}`, body);
      else await api.post("/api/knowledge", body);
      toast(t("kb.saved"), "success");
      setForm(null);
      load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function toggleSelect(id: string) {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function bulkDelete() {
    if (selected.size === 0) return;
    if (!(await confirm(t("kb.deleteConfirm", { count: selected.size })))) return;
    try {
      const res = await api.post<{ deleted: number }>("/api/knowledge/bulk-delete", { ids: [...selected] });
      toast(t("bulk.deleted", { count: res.deleted }), "success");
      setSelected(new Set());
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("kb.title")}</h1>
        <button
          onClick={() => { setError(null); setForm({ ...EMPTY_FORM, machine_label: machineFilter }); }}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
        >
          {t("kb.new")}
        </button>
        <select
          value={machineFilter}
          onChange={(e) => setMachineFilter(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          <option value="">{t("kb.allMachines")}</option>
          {machines.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("kb.search")}
          className="min-w-52 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
        />
        {canDelete && selected.size > 0 && (
          <button
            onClick={bulkDelete}
            className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700"
          >
            {t("bulk.deleteSelected", { count: selected.size })}
          </button>
        )}
      </div>

      <p className="mb-3 text-sm text-slate-500">{t("kb.hint")}</p>

      <IconLegend items={[{ icon: "✏️", label: t("common.edit") }]} />
      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-slate-500">
              {canDelete && (
                <th className="sticky top-0 z-10 w-8 border-b border-slate-200 bg-white px-3 py-3">
                  <input
                    type="checkbox"
                    checked={entries.length > 0 && entries.every((e) => selected.has(e.id))}
                    onChange={(e) =>
                      setSelected(e.target.checked ? new Set(entries.map((x) => x.id)) : new Set())
                    }
                    className="h-4 w-4"
                  />
                </th>
              )}
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("kb.entry")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("kb.solution")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("kb.author")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id} className="border-b border-slate-100 align-top last:border-0">
                {canDelete && (
                  <td className="px-3 py-3">
                    <input
                      type="checkbox"
                      checked={selected.has(e.id)}
                      onChange={() => toggleSelect(e.id)}
                      className="h-4 w-4"
                    />
                  </td>
                )}
                <td className="px-4 py-3">
                  <div className="font-medium">{e.title}</div>
                  <div className="text-xs font-semibold text-indigo-700">{e.machine_label}</div>
                </td>
                <td className="max-w-md px-4 py-3">
                  <p className="line-clamp-3 whitespace-pre-wrap text-slate-600">{e.content}</p>
                </td>
                <td className="px-4 py-3 text-slate-500">
                  <div>{e.created_by_name ?? "—"}</div>
                  <div className="text-xs text-slate-400">{fmt(e.updated_at)}</div>
                </td>
                <td className="px-4 py-3 text-right">
                  <button
                    onClick={() => {
                      setError(null);
                      setForm({ id: e.id, machine_label: e.machine_label, title: e.title, content: e.content });
                    }}
                    title={t("common.edit")}
                    className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                  >
                    ✏️
                  </button>
                </td>
              </tr>
            ))}
            {entries.length === 0 && (
              <tr>
                <td colSpan={canDelete ? 5 : 4} className="px-4 py-10 text-center text-slate-400">
                  {t("kb.empty")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {form && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) setForm(null); }}
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4"
        >
          <form onSubmit={save} className="my-8 w-full max-w-lg space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{form.id ? t("kb.editTitle") : t("kb.newTitle")}</h2>
            <label className="block text-sm">
              {t("kb.machine")} *
              <input
                required
                list="kb-machines"
                value={form.machine_label}
                onChange={(e) => setForm({ ...form, machine_label: e.target.value })}
                placeholder={t("kb.machinePh")}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
              <datalist id="kb-machines">
                {machines.map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
            </label>
            <label className="block text-sm">
              {t("kb.entryTitle")} *
              <input
                required
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
                placeholder={t("kb.titlePh")}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="block text-sm">
              {t("kb.solution")} *
              <textarea
                required
                minLength={5}
                value={form.content}
                onChange={(e) => setForm({ ...form, content: e.target.value })}
                rows={6}
                placeholder={t("kb.solutionPh")}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setForm(null)}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100"
              >
                {t("common.cancel")}
              </button>
              <button
                disabled={busy}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {busy ? t("common.saving") : t("common.save")}
              </button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
