"use client";

/** Automatizálások (Flow-szerű): trigger → feltételek → akciók.
 *  A triggerek és az elérhető {{változók}} a backend katalógusából jönnek. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Condition {
  field: string;
  op: string;
  value: string;
}

interface Action {
  type: string;
  template_id?: string;
  to?: string;
  text?: string;
  title?: string;
  description?: string;
  employee?: string;
}

interface Rule {
  id: string;
  name: string;
  trigger: string;
  enabled: boolean;
  conditions: Condition[];
  actions: Action[];
  run_count: number;
  last_run_at: string | null;
  last_error: string | null;
}

interface Template {
  id: string;
  name: string;
  subject: string;
}

interface Catalog {
  triggers: Record<string, string[]>;
  ops: string[];
  actions: string[];
}

const EMPTY_RULE: Omit<Rule, "id" | "run_count" | "last_run_at" | "last_error"> = {
  name: "",
  trigger: "settlement.created",
  enabled: true,
  conditions: [],
  actions: [{ type: "send_email", to: "recipients" }],
};

export default function AutomatizalasokPage() {
  const { t, lang } = useT();
  const { toast, confirm } = useUI();
  const [rules, setRules] = useState<Rule[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [form, setForm] = useState<(typeof EMPTY_RULE & { id?: string }) | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get<Rule[]>("/api/automation/rules").then(setRules).catch(() => {});
    api.get<Template[]>("/api/automation/templates").then(setTemplates).catch(() => {});
    api.get<Catalog>("/api/automation/triggers").then(setCatalog).catch(() => {});
  }, []);
  useEffect(load, [load]);

  const fmt = (iso: string | null) =>
    iso ? new Date(iso).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB") : "—";

  const vars = catalog?.triggers[form?.trigger ?? ""] ?? [];

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!form) return;
    setBusy(true);
    setError(null);
    try {
      const body = {
        name: form.name,
        trigger: form.trigger,
        enabled: form.enabled,
        conditions: form.conditions.filter((c) => c.field),
        actions: form.actions,
      };
      if (form.id) await api.patch(`/api/automation/rules/${form.id}`, body);
      else await api.post("/api/automation/rules", body);
      setForm(null);
      load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function toggleRule(r: Rule) {
    try {
      await api.patch(`/api/automation/rules/${r.id}`, {
        name: r.name, trigger: r.trigger, enabled: !r.enabled,
        conditions: r.conditions, actions: r.actions,
      });
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function removeRule(r: Rule) {
    if (!(await confirm(t("auto.deleteConfirm", { name: r.name })))) return;
    try {
      await api.delete(`/api/automation/rules/${r.id}`);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  function setCondition(i: number, patch: Partial<Condition>) {
    if (!form) return;
    const next = [...form.conditions];
    next[i] = { ...next[i], ...patch };
    setForm({ ...form, conditions: next });
  }

  function setAction(i: number, patch: Partial<Action>) {
    if (!form) return;
    const next = [...form.actions];
    next[i] = { ...next[i], ...patch };
    setForm({ ...form, actions: next });
  }

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("auto.title")}</h1>
        <button
          onClick={() => { setError(null); setForm({ ...EMPTY_RULE, conditions: [], actions: [{ type: "send_email", to: "recipients" }] }); }}
          className="ml-auto rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
        >
          {t("auto.newRule")}
        </button>
      </div>
      <p className="mb-4 text-sm text-slate-500">{t("auto.hint")}</p>

      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-slate-500">
              <th className="border-b border-slate-200 px-4 py-3">{t("auto.name")}</th>
              <th className="border-b border-slate-200 px-4 py-3">{t("auto.trigger")}</th>
              <th className="border-b border-slate-200 px-4 py-3">{t("auto.actions")}</th>
              <th className="border-b border-slate-200 px-4 py-3">{t("auto.runs")}</th>
              <th className="border-b border-slate-200 px-4 py-3">{t("auto.lastRun")}</th>
              <th className="border-b border-slate-200 px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {rules.map((r) => (
              <tr key={r.id} className="border-b border-slate-100 last:border-0">
                <td className="px-4 py-3">
                  <div className="font-medium">{r.name}</div>
                  {r.last_error && (
                    <div className="text-xs text-red-600" title={r.last_error}>
                      ⚠ {r.last_error.slice(0, 60)}
                    </div>
                  )}
                </td>
                <td className="px-4 py-3">
                  <span className="rounded bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-800">
                    {t(`auto.triggers.${r.trigger.replace(/\./g, "_")}`)}
                  </span>
                  {r.conditions.length > 0 && (
                    <span className="ml-1 text-xs text-slate-400">
                      +{r.conditions.length} {t("auto.conditionsShort")}
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-xs text-slate-600">
                  {r.actions.map((a, i) => (
                    <span key={i} className="mr-1 rounded bg-slate-100 px-2 py-0.5">
                      {t(`auto.actionTypes.${a.type}`)}
                    </span>
                  ))}
                </td>
                <td className="px-4 py-3 text-slate-500">{r.run_count}</td>
                <td className="px-4 py-3 text-slate-500">{fmt(r.last_run_at)}</td>
                <td className="px-4 py-3 text-right">
                  <div className="flex justify-end gap-1.5">
                    <button
                      onClick={() => toggleRule(r)}
                      className={`rounded px-2 py-1 text-xs font-medium ${r.enabled ? "bg-emerald-100 text-emerald-800 hover:bg-emerald-200" : "bg-slate-200 text-slate-600 hover:bg-slate-300"}`}
                    >
                      {r.enabled ? t("auto.on") : t("auto.off")}
                    </button>
                    <button
                      onClick={() => { setError(null); setForm({ ...r }); }}
                      title={t("common.edit")}
                      className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                    >
                      ✏️
                    </button>
                    <button
                      onClick={() => removeRule(r)}
                      title={t("common.delete")}
                      className="rounded border border-red-200 px-2 py-1 text-sm leading-none text-red-600 hover:bg-red-50"
                    >
                      🗑
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {rules.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-10 text-center text-slate-400">{t("auto.noRules")}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {form && catalog && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) setForm(null); }}
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4"
        >
          <form onSubmit={save} className="my-8 w-full max-w-2xl space-y-4 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{form.id ? t("auto.editRule") : t("auto.newRule")}</h2>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                {t("auto.name")} *
                <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
              <label className="block text-sm">
                {t("auto.trigger")}
                <select
                  value={form.trigger}
                  onChange={(e) => setForm({ ...form, trigger: e.target.value, conditions: [] })}
                  className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2"
                >
                  {Object.keys(catalog.triggers).map((k) => (
                    <option key={k} value={k}>{t(`auto.triggers.${k.replace(/\./g, "_")}`)}</option>
                  ))}
                </select>
              </label>
            </div>
            <p className="text-xs text-slate-400">
              {t("auto.varsHint")}: {vars.map((v) => `{{${v}}}`).join(", ")}
            </p>

            <fieldset className="space-y-2 rounded-xl border border-slate-200 p-3">
              <legend className="px-1 text-xs font-semibold uppercase text-slate-400">{t("auto.conditions")}</legend>
              {form.conditions.map((c, i) => (
                <div key={i} className="flex flex-wrap items-center gap-2">
                  <select value={c.field} onChange={(e) => setCondition(i, { field: e.target.value })} className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm">
                    <option value="">—</option>
                    {vars.map((v) => <option key={v} value={v}>{v}</option>)}
                  </select>
                  <select value={c.op} onChange={(e) => setCondition(i, { op: e.target.value })} className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm">
                    {catalog.ops.map((o) => <option key={o} value={o}>{t(`auto.ops.${o}`)}</option>)}
                  </select>
                  <input value={c.value} onChange={(e) => setCondition(i, { value: e.target.value })} className="min-w-32 flex-1 rounded-lg border border-slate-300 px-2 py-1.5 text-sm" />
                  <button type="button" onClick={() => setForm({ ...form, conditions: form.conditions.filter((_, j) => j !== i) })} className="text-red-500 hover:text-red-700">✕</button>
                </div>
              ))}
              <button
                type="button"
                onClick={() => setForm({ ...form, conditions: [...form.conditions, { field: "", op: "eq", value: "" }] })}
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs hover:bg-slate-100"
              >
                + {t("auto.addCondition")}
              </button>
              {form.conditions.length === 0 && (
                <p className="text-xs text-slate-400">{t("auto.noConditions")}</p>
              )}
            </fieldset>

            <fieldset className="space-y-3 rounded-xl border border-slate-200 p-3">
              <legend className="px-1 text-xs font-semibold uppercase text-slate-400">{t("auto.actions")}</legend>
              {form.actions.map((a, i) => (
                <div key={i} className="space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
                  <div className="flex items-center gap-2">
                    <select value={a.type} onChange={(e) => setAction(i, { type: e.target.value })} className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm font-medium">
                      {catalog.actions.map((x) => <option key={x} value={x}>{t(`auto.actionTypes.${x}`)}</option>)}
                    </select>
                    {form.actions.length > 1 && (
                      <button type="button" onClick={() => setForm({ ...form, actions: form.actions.filter((_, j) => j !== i) })} className="ml-auto text-red-500 hover:text-red-700">✕</button>
                    )}
                  </div>
                  {a.type === "send_email" && (
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <label className="block text-xs">
                        {t("auto.emailTemplate")}
                        <select
                          required
                          value={a.template_id ?? ""}
                          onChange={(e) => setAction(i, { template_id: e.target.value })}
                          className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm"
                        >
                          <option value="">—</option>
                          {templates.map((tp) => <option key={tp.id} value={tp.id}>{tp.name}</option>)}
                        </select>
                        {templates.length === 0 && (
                          <span className="mt-1 block text-amber-600">{t("auto.noTemplates")}</span>
                        )}
                      </label>
                      <label className="block text-xs">
                        {t("auto.emailTo")}
                        <input
                          value={a.to ?? ""}
                          onChange={(e) => setAction(i, { to: e.target.value })}
                          placeholder="partner | agent | recipients | cim@ceg.hu"
                          className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                        />
                        <span className="mt-0.5 block text-slate-400">{t("auto.emailToHint")}</span>
                      </label>
                    </div>
                  )}
                  {a.type === "send_whatsapp" && (
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <label className="block text-xs sm:col-span-2">
                        {t("auto.waText")}
                        <input
                          value={a.text ?? ""}
                          onChange={(e) => setAction(i, { text: e.target.value })}
                          placeholder={t("auto.waTextPh")}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                        />
                      </label>
                      <label className="block text-xs">
                        {t("auto.waTo")}
                        <input
                          value={a.to ?? ""}
                          onChange={(e) => setAction(i, { to: e.target.value })}
                          placeholder="recipients | agent | 36301234567"
                          className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                        />
                        <span className="mt-0.5 block text-slate-400">{t("auto.waToHint")}</span>
                      </label>
                    </div>
                  )}
                  {a.type === "send_telegram" && (
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <label className="block text-xs sm:col-span-2">
                        {t("auto.waText")}
                        <input
                          value={a.text ?? ""}
                          onChange={(e) => setAction(i, { text: e.target.value })}
                          placeholder={t("auto.waTextPh")}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                        />
                      </label>
                      <label className="block text-xs">
                        {t("auto.tgTo")}
                        <input
                          value={a.to ?? ""}
                          onChange={(e) => setAction(i, { to: e.target.value })}
                          placeholder="recipients | -100123456789"
                          className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                        />
                        <span className="mt-0.5 block text-slate-400">{t("auto.tgToHint")}</span>
                      </label>
                    </div>
                  )}
                  {a.type === "add_partner_note" && (
                    <label className="block text-xs">
                      {t("auto.noteText")}
                      <input
                        value={a.text ?? ""}
                        onChange={(e) => setAction(i, { text: e.target.value })}
                        placeholder={t("auto.noteTextPh")}
                        className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                      />
                    </label>
                  )}
                  {a.type === "create_task" && (
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <label className="block text-xs">
                        {t("auto.taskTitle")}
                        <input value={a.title ?? ""} onChange={(e) => setAction(i, { title: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm" />
                      </label>
                      <label className="block text-xs">
                        {t("auto.taskEmployee")}
                        <input value={a.employee ?? ""} onChange={(e) => setAction(i, { employee: e.target.value })} placeholder={t("auto.taskEmployeePh")} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm" />
                      </label>
                      <label className="block text-xs sm:col-span-2">
                        {t("auto.taskDesc")}
                        <input value={a.description ?? ""} onChange={(e) => setAction(i, { description: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm" />
                      </label>
                    </div>
                  )}
                </div>
              ))}
              <button
                type="button"
                onClick={() => setForm({ ...form, actions: [...form.actions, { type: "add_partner_note", text: "" }] })}
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs hover:bg-slate-100"
              >
                + {t("auto.addAction")}
              </button>
            </fieldset>

            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} className="h-4 w-4" />
              {t("auto.enabled")}
            </label>

            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{busy ? t("common.saving") : t("common.save")}</button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
