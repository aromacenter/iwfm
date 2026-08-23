"use client";

/** Szerviz: hibajegyek + karbantartás. Karbantartás-esedékesség a gép
 *  számláló/norma alapján; jegy státusz-léptetés (felvéve → folyamatban →
 *  kész), kiosztás szervizesnek, tömeges törlés (admin). */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import SearchSelect from "@/components/SearchSelect";
import PartnerInfo from "@/components/PartnerInfo";
import IconLegend from "@/components/IconLegend";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { usePerms } from "@/lib/perms";
import { useUI } from "@/lib/ui";

interface Ticket {
  id: string;
  ticket_no: string;
  kind: "repair" | "maintenance";
  status: "open" | "in_progress" | "done" | "cancelled";
  priority: "low" | "normal" | "high";
  title: string;
  description: string | null;
  asset_id: string | null;
  asset_label: string | null;
  partner_id: string | null;
  partner_label: string | null;
  assigned_to_user_id: string | null;
  assigned_to_name: string | null;
  counter_at_service: number | null;
  resolution: string | null;
  parts: { name: string; qty: number; unit_price: number }[] | null;
  labor_fee: number | null;
  total_cost: number;
  resolved_at: string | null;
  created_at: string;
}

interface Assignee {
  id: string;
  name: string;
  role: string;
}

interface AssetOption {
  id: string;
  barcode: string;
  name: string;
  partner_name: string | null;
}

interface MaintenanceDue {
  asset_id: string;
  barcode: string;
  name: string;
  partner_name: string | null;
  counter: number;
  norm: number;
  since_last_service: number;
  overdue_by: number;
}

const STATUSES = ["open", "in_progress", "done", "cancelled"] as const;
const KINDS = ["repair", "maintenance"] as const;
const PRIORITIES = ["low", "normal", "high"] as const;

const STATUS_STYLE: Record<Ticket["status"], string> = {
  open: "bg-amber-100 text-amber-800",
  in_progress: "bg-blue-100 text-blue-800",
  done: "bg-emerald-100 text-emerald-800",
  cancelled: "bg-slate-100 text-slate-500",
};

const PRIORITY_STYLE: Record<Ticket["priority"], string> = {
  low: "text-slate-400",
  normal: "text-slate-600",
  high: "text-red-600 font-semibold",
};

interface PartRow {
  name: string;
  qty: string;
  unit_price: string;
}

interface FormState {
  id: string | null;
  title: string;
  kind: (typeof KINDS)[number];
  priority: (typeof PRIORITIES)[number];
  description: string;
  asset_id: string;
  assigned_to_user_id: string;
  resolution: string;
  counter_at_service: string;
  parts: PartRow[];
  labor_fee: string;
}

const EMPTY_FORM: FormState = {
  id: null,
  title: "",
  kind: "repair",
  priority: "normal",
  description: "",
  asset_id: "",
  assigned_to_user_id: "",
  resolution: "",
  counter_at_service: "",
  parts: [],
  labor_fee: "",
};

export default function SzervizPage() {
  const { t, lang } = useT();
  const { toast, confirm } = useUI();
  const canDelete = usePerms().can("delete");
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [infoPartner, setInfoPartner] = useState<string | null>(null);
  const [assignees, setAssignees] = useState<Assignee[]>([]);
  const [assets, setAssets] = useState<AssetOption[]>([]);
  const [maintDue, setMaintDue] = useState<MaintenanceDue[]>([]);
  const [showDue, setShowDue] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [search, setSearch] = useState("");
  const [form, setForm] = useState<FormState | null>(null);
  const [attachments, setAttachments] = useState<{ id: string; filename: string }[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fmt = (dt: string) =>
    new Date(dt).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "short", timeStyle: "short" });

  const load = useCallback(() => {
    const params = new URLSearchParams();
    if (statusFilter) params.set("status", statusFilter);
    if (search.trim()) params.set("q", search.trim());
    const qs = params.toString();
    api.get<Ticket[]>(`/api/service${qs ? `?${qs}` : ""}`).then(setTickets).catch(() => {});
  }, [statusFilter, search]);

  const loadDue = useCallback(() => {
    api.get<MaintenanceDue[]>("/api/service/maintenance-due").then(setMaintDue).catch(() => {});
  }, []);

  // Karbantartás-alapvonal: az előzmény nélküli gépek jelenlegi állását
  // "kész" jegyként rögzíti — az esedékesség innentől reális.
  const [baselineBusy, setBaselineBusy] = useState(false);

  async function runBaseline() {
    if (!(await confirm(t("service.baselineConfirm", { count: maintDue.length })))) return;
    setBaselineBusy(true);
    try {
      const res = await api.post<{ created: number }>("/api/service/maintenance-baseline");
      toast(t("service.baselineDone", { count: res.created }), "success");
      loadDue();
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBaselineBusy(false);
    }
  }

  useEffect(load, [load]);
  useEffect(loadDue, [loadDue]);
  useEffect(() => {
    api.get<Assignee[]>("/api/service/assignees").then(setAssignees).catch(() => {});
    api.get<AssetOption[]>("/api/assets").then(setAssets).catch(() => {});
  }, []);

  function openNew(preset?: Partial<FormState>) {
    setError(null);
    setForm({ ...EMPTY_FORM, ...preset });
  }

  function openEdit(tk: Ticket) {
    setError(null);
    setAttachments([]);
    api.get<{ id: string; filename: string }[]>(`/api/service/${tk.id}/attachments`)
      .then(setAttachments)
      .catch(() => {});
    setForm({
      id: tk.id,
      title: tk.title,
      kind: tk.kind,
      priority: tk.priority,
      description: tk.description ?? "",
      asset_id: tk.asset_id ?? "",
      assigned_to_user_id: tk.assigned_to_user_id ?? "",
      resolution: tk.resolution ?? "",
      counter_at_service: tk.counter_at_service?.toString() ?? "",
      parts: (tk.parts ?? []).map((p) => ({
        name: p.name ?? "",
        qty: String(p.qty ?? 1),
        unit_price: String(p.unit_price ?? 0),
      })),
      labor_fee: tk.labor_fee != null ? String(tk.labor_fee) : "",
    });
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!form) return;
    setBusy(true);
    setError(null);
    try {
      if (form.id) {
        await api.patch(`/api/service/${form.id}`, {
          title: form.title,
          kind: form.kind,
          priority: form.priority,
          description: form.description || null,
          assigned_to_user_id: form.assigned_to_user_id || "",
          resolution: form.resolution || null,
          counter_at_service: form.counter_at_service === "" ? null : Number(form.counter_at_service),
          parts: form.parts
            .filter((p) => p.name.trim())
            .map((p) => ({
              name: p.name.trim(),
              qty: Number(p.qty) || 1,
              unit_price: Number(p.unit_price) || 0,
            })),
          labor_fee: form.labor_fee === "" ? null : Number(form.labor_fee),
        });
      } else {
        await api.post("/api/service", {
          title: form.title,
          kind: form.kind,
          priority: form.priority,
          description: form.description || null,
          asset_id: form.asset_id || null,
          assigned_to_user_id: form.assigned_to_user_id || null,
        });
      }
      setForm(null);
      load();
      loadDue();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(tk: Ticket, status: Ticket["status"]) {
    try {
      await api.patch(`/api/service/${tk.id}`, { status });
      load();
      loadDue();
    } catch (err) {
      toast(errorMessage(err), "error");
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
    if (!(await confirm(t("service.deleteConfirm", { count: selected.size })))) return;
    try {
      const res = await api.post<{ deleted: number }>("/api/service/bulk-delete", { ids: [...selected] });
      toast(t("bulk.deleted", { count: res.deleted }), "success");
      setSelected(new Set());
      load();
      loadDue();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  function createMaintenanceTicket(d: MaintenanceDue) {
    openNew({
      kind: "maintenance",
      title: t("service.maintTitle", { name: d.name, barcode: d.barcode }),
      asset_id: d.asset_id,
      description: t("service.maintDesc", { counter: d.counter, norm: d.norm }),
    });
  }

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("service.title")}</h1>
        <button
          onClick={() => openNew()}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
        >
          {t("service.new")}
        </button>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          <option value="">{t("service.allStatuses")}</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>{t(`service.statuses.${s}`)}</option>
          ))}
        </select>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("service.search")}
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

      {maintDue.length > 0 && (
        <div className="mb-6 rounded-2xl border border-amber-200 bg-amber-50 shadow-sm">
          <button
            onClick={() => setShowDue((v) => !v)}
            className="flex w-full items-center justify-between px-4 py-3 text-left"
          >
            <span className="font-semibold text-amber-900">{t("service.dueTitle", { count: maintDue.length })}</span>
            <span className="text-amber-700">{showDue ? "▾" : "▸"}</span>
          </button>
          {showDue && (
            <div className="border-t border-amber-200">
              <div className="flex items-center gap-2 px-4 py-2">
                <button
                  onClick={runBaseline}
                  disabled={baselineBusy}
                  title={t("service.baselineHint")}
                  className="rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-medium text-amber-800 hover:bg-amber-100 disabled:opacity-50"
                >
                  {baselineBusy ? t("common.saving") : t("service.baselineBtn")}
                </button>
                <span className="text-xs text-amber-700">{t("service.baselineExplain")}</span>
              </div>
              <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-amber-700">
                    <th className="px-4 py-2">{t("service.machine")}</th>
                    <th className="px-4 py-2">{t("service.partner")}</th>
                    <th className="px-4 py-2">{t("service.counter")}</th>
                    <th className="px-4 py-2">{t("service.norm")}</th>
                    <th className="px-4 py-2">{t("service.sinceService")}</th>
                    <th className="px-4 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {maintDue.map((d) => (
                    <tr key={d.asset_id} className="border-t border-amber-100">
                      <td className="px-4 py-2 font-medium text-amber-950">
                        {d.name} <span className="text-xs text-amber-600">{d.barcode}</span>
                      </td>
                      <td className="px-4 py-2 text-amber-800">{d.partner_name ?? "—"}</td>
                      <td className="px-4 py-2 text-amber-800">{d.counter}</td>
                      <td className="px-4 py-2 text-amber-800">{d.norm}</td>
                      <td className="px-4 py-2 text-amber-800">{d.since_last_service}</td>
                      <td className="px-4 py-2 text-right">
                        <button
                          onClick={() => createMaintenanceTicket(d)}
                          className="rounded bg-amber-600 px-3 py-1 text-xs font-medium text-white hover:bg-amber-700"
                        >
                          {t("service.createMaint")}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            </div>
          )}
        </div>
      )}

      <IconLegend
        items={[
          { icon: "▶️", label: t("service.start") },
          { icon: "✅", label: t("service.finish") },
          { icon: "🚫", label: t("service.cancel") },
          { icon: "🔄", label: t("service.reopen") },
        ]}
      />
      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-slate-500">
              {canDelete && (
                <th className="sticky top-0 z-10 w-8 border-b border-slate-200 bg-white px-3 py-3">
                  <input
                    type="checkbox"
                    checked={tickets.length > 0 && tickets.every((tk) => selected.has(tk.id))}
                    onChange={(e) =>
                      setSelected(e.target.checked ? new Set(tickets.map((tk) => tk.id)) : new Set())
                    }
                    className="h-4 w-4"
                  />
                </th>
              )}
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("service.titleCol")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("service.machine")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("service.assignee")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("service.status")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {tickets.map((tk) => (
              <tr key={tk.id} className="border-b border-slate-100 last:border-0">
                {canDelete && (
                  <td className="px-3 py-3">
                    <input
                      type="checkbox"
                      checked={selected.has(tk.id)}
                      onChange={() => toggleSelect(tk.id)}
                      className="h-4 w-4"
                    />
                  </td>
                )}
                <td className="px-4 py-3">
                  <button onClick={() => openEdit(tk)} className="font-medium text-indigo-700 hover:underline">
                    {tk.title}
                  </button>
                  <div className="text-xs text-slate-400">
                    <span className="font-mono">{tk.ticket_no}</span> · {fmt(tk.created_at)} ·{" "}
                    {t(`service.kinds.${tk.kind}`)}
                    {tk.priority !== "normal" && (
                      <span className={`ml-1 ${PRIORITY_STYLE[tk.priority]}`}>
                        · {t(`service.priorities.${tk.priority}`)}
                      </span>
                    )}
                  </div>
                </td>
                <td className="px-4 py-3">
                  {tk.asset_label ?? "—"}
                  {tk.partner_label && (
                    tk.partner_id ? (
                      <button
                        onClick={() => setInfoPartner(tk.partner_id)}
                        className="block text-left text-xs text-indigo-600 hover:underline"
                      >
                        {tk.partner_label}
                      </button>
                    ) : (
                      <div className="text-xs text-slate-400">{tk.partner_label}</div>
                    )
                  )}
                </td>
                <td className="px-4 py-3">{tk.assigned_to_name ?? "—"}</td>
                <td className="px-4 py-3">
                  <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[tk.status]}`}>
                    {t(`service.statuses.${tk.status}`)}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="flex items-center justify-end gap-1.5">
                    {tk.status === "open" && (
                      <button
                        onClick={() => setStatus(tk, "in_progress")}
                        title={t("service.start")}
                        className="rounded border border-blue-300 px-2 py-1 text-sm leading-none hover:bg-blue-50"
                      >
                        ▶️
                      </button>
                    )}
                    {(tk.status === "open" || tk.status === "in_progress") && (
                      <>
                        <button
                          onClick={() => setStatus(tk, "done")}
                          title={t("service.finish")}
                          className="rounded border border-emerald-300 px-2 py-1 text-sm leading-none hover:bg-emerald-50"
                        >
                          ✅
                        </button>
                        <button
                          onClick={() => setStatus(tk, "cancelled")}
                          title={t("service.cancel")}
                          className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                        >
                          🚫
                        </button>
                      </>
                    )}
                    {(tk.status === "done" || tk.status === "cancelled") && (
                      <button
                        onClick={() => setStatus(tk, "open")}
                        title={t("service.reopen")}
                        className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                      >
                        🔄
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {tickets.length === 0 && (
              <tr>
                <td colSpan={canDelete ? 6 : 5} className="px-4 py-10 text-center text-slate-400">
                  {t("service.empty")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {form && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) setForm(null); }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
        >
          <form onSubmit={save} className="max-h-[90vh] w-full max-w-lg space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{form.id ? t("service.editTitle") : t("service.newTitle")}</h2>
            <label className="block text-sm">
              {t("service.titleCol")}
              <input
                required
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-sm">
                {t("service.kind")}
                <select
                  value={form.kind}
                  onChange={(e) => setForm({ ...form, kind: e.target.value as FormState["kind"] })}
                  className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2"
                >
                  {KINDS.map((k) => (
                    <option key={k} value={k}>{t(`service.kinds.${k}`)}</option>
                  ))}
                </select>
              </label>
              <label className="block text-sm">
                {t("service.priority")}
                <select
                  value={form.priority}
                  onChange={(e) => setForm({ ...form, priority: e.target.value as FormState["priority"] })}
                  className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2"
                >
                  {PRIORITIES.map((p) => (
                    <option key={p} value={p}>{t(`service.priorities.${p}`)}</option>
                  ))}
                </select>
              </label>
            </div>
            {!form.id && (
              <label className="block text-sm">
                {t("service.machine")}
                <SearchSelect
                  items={assets.map((a) => ({
                    id: a.id, label: a.name, sublabel: a.partner_name, badge: a.barcode,
                  }))}
                  value={form.asset_id}
                  onChange={(id) => setForm({ ...form, asset_id: id })}
                  placeholder={t("service.machineSearchPh")}
                  className="mt-1 w-full"
                />
              </label>
            )}
            <label className="block text-sm">
              {t("service.assignee")}
              <select
                value={form.assigned_to_user_id}
                onChange={(e) => setForm({ ...form, assigned_to_user_id: e.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2"
              >
                <option value="">{t("service.unassigned")}</option>
                {assignees.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              {t("service.description")}
              <textarea
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                rows={3}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            {form.id && attachments.length > 0 && (
              <div className="text-sm">
                <p className="mb-1">{t("service.attachments")}</p>
                <div className="flex flex-wrap gap-2">
                  {attachments.map((a) => (
                    <a key={a.id} href={`/api/service/attachments/${a.id}`} target="_blank" rel="noreferrer">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={`/api/service/attachments/${a.id}`}
                        alt={a.filename}
                        className="h-20 w-20 rounded-lg border border-slate-200 object-cover hover:opacity-80"
                      />
                    </a>
                  ))}
                </div>
              </div>
            )}
            {form.id && (
              <>
                <label className="block text-sm">
                  {t("service.resolution")}
                  <textarea
                    value={form.resolution}
                    onChange={(e) => setForm({ ...form, resolution: e.target.value })}
                    rows={2}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                  />
                </label>
                <label className="block text-sm">
                  {t("service.counterAtService")}
                  <input
                    type="number"
                    min={0}
                    value={form.counter_at_service}
                    onChange={(e) => setForm({ ...form, counter_at_service: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                  />
                </label>
                <fieldset className="space-y-2 rounded-xl border border-slate-200 p-3">
                  <legend className="px-1 text-xs font-semibold uppercase text-slate-400">{t("service.costSection")}</legend>
                  {form.parts.map((p, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <input
                        value={p.name}
                        onChange={(e) => setForm({ ...form, parts: form.parts.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) })}
                        placeholder={t("service.partName")}
                        className="min-w-0 flex-1 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                      />
                      <input
                        type="number" min={0} step="0.1" value={p.qty}
                        onChange={(e) => setForm({ ...form, parts: form.parts.map((x, j) => (j === i ? { ...x, qty: e.target.value } : x)) })}
                        title={t("service.partQty")}
                        className="w-16 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                      />
                      <input
                        type="number" min={0} value={p.unit_price}
                        onChange={(e) => setForm({ ...form, parts: form.parts.map((x, j) => (j === i ? { ...x, unit_price: e.target.value } : x)) })}
                        title={t("service.partPrice")}
                        className="w-24 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                      />
                      <button type="button" onClick={() => setForm({ ...form, parts: form.parts.filter((_, j) => j !== i) })} className="text-slate-400 hover:text-red-600">✕</button>
                    </div>
                  ))}
                  <div className="flex flex-wrap items-center gap-3">
                    <button
                      type="button"
                      onClick={() => setForm({ ...form, parts: [...form.parts, { name: "", qty: "1", unit_price: "0" }] })}
                      className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs hover:bg-slate-100"
                    >
                      + {t("service.partAdd")}
                    </button>
                    <label className="flex items-center gap-2 text-sm">
                      {t("service.laborFee")}
                      <input
                        type="number" min={0} value={form.labor_fee}
                        onChange={(e) => setForm({ ...form, labor_fee: e.target.value })}
                        className="w-28 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                      />
                    </label>
                    <span className="text-xs text-slate-500">
                      {t("service.costTotal", {
                        total: (
                          (Number(form.labor_fee) || 0)
                          + form.parts.reduce((s, p) => s + (Number(p.qty) || 0) * (Number(p.unit_price) || 0), 0)
                        ).toLocaleString("hu-HU"),
                      })}
                    </span>
                  </div>
                </fieldset>
              </>
            )}
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

      <PartnerInfo partnerId={infoPartner} onClose={() => setInfoPartner(null)} />
    </AppShell>
  );
}
