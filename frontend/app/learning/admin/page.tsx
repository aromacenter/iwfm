"use client";

/** X-Learning – vezetői rész (`learning` jog): tananyag-szerkesztő, kérdésbank,
 *  csatolmányok, kiosztás (egyéni + szerepkör), eredmény-tábla, áttekintés,
 *  képzés-napló CSV (bérexport-jog). Ugyanazon a felületen, mint a tanulói
 *  karton — az X-admin menüből ide mutat a link. */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api, downloadFile, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { usePerms } from "@/lib/perms";
import { useUI } from "@/lib/ui";
import { STATUS_CLS, fmtSize, type Material, type MyItem } from "../shared";

type Tab = "edit" | "questions" | "files" | "assign" | "results";

interface QOption {
  text: string;
  correct: boolean;
}
interface QEdit {
  text: string;
  options: QOption[];
}
interface EmployeeLite {
  id: string;
  last_name: string;
  first_name: string;
  status?: string;
  is_contractor?: boolean;
  role?: string | null;
}
interface ResultRow {
  assignment_id: string;
  employee_id: string;
  employee_name: string;
  is_contractor: boolean;
  status: MyItem["status"];
  due_date: string | null;
  completed_at: string | null;
  valid_until: string | null;
  attempts: number;
  best_score: number | null;
  last_attempt_at: string | null;
  source: "manual" | "role";
}
interface Overview {
  materials: number;
  assigned: number;
  done: number;
  open: number;
  overdue: number;
  expired: number;
}

interface FormState {
  title: string;
  category: string;
  body: string;
  video_url: string;
  validity_months: string;
  random_count: string;
  default_due_days: string;
  external_ok: boolean;
  is_active: boolean;
  auto_roles: string[];
}
const EMPTY: FormState = {
  title: "", category: "", body: "", video_url: "", validity_months: "", random_count: "", default_due_days: "",
  external_ok: false, is_active: true, auto_roles: [],
};
const FALLBACK_ROLES = ["employee", "manager", "szervizes", "uzletkoto", "admin"];
const inputCls = "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm";
const btnP = "rounded-lg bg-[#E31E24] px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50";
const btnG = "rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-100 disabled:opacity-50";

function toForm(m: Material): FormState {
  return {
    title: m.title, category: m.category ?? "", body: m.body ?? "", video_url: m.video_url ?? "",
    validity_months: m.validity_months?.toString() ?? "", random_count: m.random_count?.toString() ?? "",
    default_due_days: m.default_due_days?.toString() ?? "", external_ok: m.external_ok, is_active: m.is_active,
    auto_roles: m.auto_roles ?? [],
  };
}

function fromForm(f: FormState) {
  const num = (s: string) => (s.trim() ? Number(s) : null);
  return {
    title: f.title.trim(), category: f.category.trim() || null, body: f.body || null, video_url: f.video_url.trim() || null,
    validity_months: num(f.validity_months), random_count: num(f.random_count), default_due_days: num(f.default_due_days),
    external_ok: f.external_ok, is_active: f.is_active, auto_roles: f.auto_roles,
  };
}

export default function LearningAdminPage() {
  const { t, lang } = useT();
  const { toast, confirm } = useUI();
  const perms = usePerms();
  const router = useRouter();
  const [materials, setMaterials] = useState<Material[]>([]);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [selected, setSelected] = useState<string | "new" | null>(null);
  const [tab, setTab] = useState<Tab>("edit");
  const [form, setForm] = useState<FormState>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [questions, setQuestions] = useState<QEdit[]>([]);
  const [employees, setEmployees] = useState<EmployeeLite[]>([]);
  const [roles, setRoles] = useState<{ key: string; name: string }[]>([]);
  const [assignEmp, setAssignEmp] = useState<string[]>([]);
  const [assignRoles, setAssignRoles] = useState<string[]>([]);
  const [assignDue, setAssignDue] = useState("");
  const [empFilter, setEmpFilter] = useState("");
  const [results, setResults] = useState<ResultRow[]>([]);
  const now = new Date();
  const [exportYm, setExportYm] = useState(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`);

  const fd = (d: string | null) => (d ? new Date(d).toLocaleDateString(lang === "hu" ? "hu-HU" : "en-GB") : "—");
  const current = selected && selected !== "new" ? materials.find((m) => m.id === selected) ?? null : null;

  const load = useCallback(() => {
    api
      .get<Material[]>("/api/learning/materials")
      .then(setMaterials)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) router.replace("/learning/login");
        else if (err instanceof ApiError && err.status === 403) router.replace("/learning");
        else toast(errorMessage(err), "error");
      });
    api.get<Overview>("/api/learning/overview").then(setOverview).catch(() => {});
  }, [router, toast]);
  useEffect(load, [load]);
  useEffect(() => {
    api
      .get<EmployeeLite[]>("/api/employees")
      .then((rows) => setEmployees(rows.filter((e) => !e.status || e.status === "active")))
      .catch(() => {});
    api
      .get<{ key: string; name: string }[]>("/api/settings/roles")
      .then(setRoles)
      .catch(() => setRoles(FALLBACK_ROLES.map((k) => ({ key: k, name: t(`roles.${k}`) }))));
  }, [t]);

  // fül-adatok a kiválasztott tananyaghoz (az űrlapot a select() tölti)
  useEffect(() => {
    if (!current) return;
    if (tab === "questions") {
      api
        .get<{ id: string; text: string; options: QOption[] }[]>(`/api/learning/materials/${current.id}/questions`)
        .then((rows) => setQuestions(rows.map((q) => ({ text: q.text, options: q.options.map((o) => ({ text: o.text, correct: !!o.correct })) }))))
        .catch((err) => toast(errorMessage(err), "error"));
    } else if (tab === "results") {
      api.get<ResultRow[]>(`/api/learning/materials/${current.id}/results`).then(setResults).catch((err) => toast(errorMessage(err), "error"));
    }
  }, [current, tab, toast]);

  function select(id: string | "new") {
    setSelected(id);
    const m = id === "new" ? null : materials.find((x) => x.id === id) ?? null;
    setForm(m ? toForm(m) : EMPTY);
    setQuestions([]);
    setResults([]);
    setTab("edit");
    setAssignEmp([]);
    setAssignRoles([]);
    setAssignDue("");
  }

  async function saveMaterial(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const body = fromForm(form);
      if (current) {
        await api.patch(`/api/learning/materials/${current.id}`, body);
      } else {
        const created = await api.post<Material>("/api/learning/materials", body);
        setSelected(created.id);
      }
      toast(t("learning.admin.saved"), "success");
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function deleteMaterial() {
    if (!current || !(await confirm(t("learning.admin.deleteConfirm", { title: current.title })))) return;
    try {
      await api.delete(`/api/learning/materials/${current.id}`);
      toast(t("learning.admin.deleted"), "success");
      setSelected(null);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function saveQuestions() {
    if (!current) return;
    setBusy(true);
    try {
      const cleaned = questions
        .map((q) => ({ text: q.text.trim(), options: q.options.filter((o) => o.text.trim()).map((o) => ({ text: o.text.trim(), correct: o.correct })) }))
        .filter((q) => q.text);
      await api.put(`/api/learning/materials/${current.id}/questions`, { questions: cleaned });
      toast(t("learning.admin.questions.saved"), "success");
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  function uploadFile(file: File) {
    if (!current) return;
    if (file.size > 20 * 1024 * 1024) {
      toast(t("learning.admin.files.tooLarge"), "error");
      return;
    }
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        await api.post(`/api/learning/materials/${current.id}/attachments`, { filename: file.name, data_url: String(reader.result) });
        toast(t("learning.admin.files.uploaded"), "success");
        load();
      } catch (err) {
        toast(errorMessage(err), "error");
      }
    };
    reader.readAsDataURL(file);
  }

  async function deleteFile(attId: string) {
    if (!current || !(await confirm(t("learning.admin.files.deleteConfirm")))) return;
    try {
      await api.delete(`/api/learning/materials/${current.id}/attachments/${attId}`);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function assign() {
    if (!current) return;
    setBusy(true);
    try {
      const r = await api.post<{ created: number; already: number }>(`/api/learning/materials/${current.id}/assign`, {
        employee_ids: assignEmp, roles: assignRoles, due_date: assignDue || null,
      });
      toast(t("learning.admin.assign.done", { created: r.created, already: r.already }), "success");
      setAssignEmp([]);
      setAssignRoles([]);
      load();
      setTab("results");
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function unassign(row: ResultRow) {
    if (!current || !(await confirm(t("learning.admin.results.unassignConfirm", { name: row.employee_name })))) return;
    try {
      await api.delete(`/api/learning/materials/${current.id}/assignments/${row.assignment_id}`);
      setResults((rows) => rows.filter((r) => r.assignment_id !== row.assignment_id));
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  function exportCsv() {
    const [y, m] = exportYm.split("-");
    downloadFile(`/api/learning/export.csv?year=${y}&month=${Number(m)}`, `kepzes_naplo_${exportYm}.csv`).catch((err) => toast(errorMessage(err), "error"));
  }

  if (perms.loaded && !perms.can("learning")) {
    return (
      <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        {t("learning.admin.forbidden")}{" "}
        <Link href="/learning" className="underline">
          {t("learning.admin.backToMy")}
        </Link>
      </div>
    );
  }

  const tabs: Tab[] = ["edit", "questions", "files", "assign", "results"];
  const filteredEmp = employees.filter((e) => `${e.last_name} ${e.first_name}`.toLowerCase().includes(empFilter.toLowerCase()));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">{t("learning.admin.title")}</h1>
        {perms.can("payroll") && (
          <div className="flex items-center gap-2 text-sm">
            <input type="month" value={exportYm} onChange={(e) => setExportYm(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1 text-sm" />
            <button type="button" onClick={exportCsv} className={btnG}>
              ⬇ {t("learning.admin.export")}
            </button>
          </div>
        )}
      </div>

      {overview && (
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
          {(["materials", "assigned", "open", "done", "overdue", "expired"] as (keyof Overview)[]).map((k) => (
            <div key={k} className="rounded-xl border border-slate-200 bg-white p-3 text-center shadow-sm">
              <div className="text-xl font-semibold">{overview[k]}</div>
              <div className="text-[11px] uppercase tracking-wide text-slate-500">{t(`learning.admin.overview.${k}`)}</div>
            </div>
          ))}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-[280px_1fr]">
        <aside className="space-y-2">
          <button type="button" onClick={() => select("new")} className={`w-full ${btnP}`}>
            {t("learning.admin.new")}
          </button>
          {materials.length === 0 && <p className="text-xs text-slate-500">{t("learning.admin.noMaterials")}</p>}
          {materials.map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() => select(m.id)}
              className={`w-full rounded-xl border p-3 text-left text-sm shadow-sm ${
                selected === m.id ? "border-slate-900 bg-white" : "border-slate-200 bg-white hover:border-slate-400"
              } ${m.is_active ? "" : "opacity-60"}`}
            >
              <div className="font-medium">
                {m.title}
                {!m.is_active && <span className="ml-1 text-xs text-slate-400">({t("learning.admin.inactive")})</span>}
              </div>
              <div className="mt-1 flex flex-wrap gap-1 text-[11px] text-slate-500">
                {m.category && <span>{m.category}</span>}
                <span>· {t("learning.admin.questionsN", { n: m.question_count })}</span>
                <span>· {t("learning.admin.assignedN", { n: m.assigned ?? 0 })}</span>
                {(m.overdue ?? 0) + (m.expired ?? 0) > 0 && <span className="text-rose-600">· {(m.overdue ?? 0) + (m.expired ?? 0)} ⚠</span>}
              </div>
            </button>
          ))}
        </aside>

        <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          {!selected && <p className="text-sm text-slate-500">{t("learning.admin.selectHint")}</p>}
          {selected && (
            <>
              <div className="mb-4 flex flex-wrap gap-1 border-b border-slate-200">
                {tabs.map((k) => (
                  <button
                    key={k}
                    type="button"
                    disabled={!current && k !== "edit"}
                    onClick={() => setTab(k)}
                    className={`-mb-px border-b-2 px-3 py-2 text-sm ${
                      tab === k ? "border-[#E31E24] font-medium text-slate-900" : "border-transparent text-slate-500 hover:text-slate-800"
                    } disabled:opacity-40`}
                  >
                    {t(`learning.admin.tabs.${k}`)}
                  </button>
                ))}
              </div>

              {tab === "edit" && (
                <form onSubmit={saveMaterial} className="space-y-3">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="block text-sm sm:col-span-2">
                      {t("learning.admin.fields.title")}
                      <input required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} className={inputCls} />
                    </label>
                    <label className="block text-sm">
                      {t("learning.admin.fields.category")}
                      <input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} className={inputCls} placeholder={t("learning.admin.fields.categoryHint")} />
                    </label>
                    <label className="block text-sm">
                      {t("learning.admin.fields.video")}
                      <input value={form.video_url} onChange={(e) => setForm({ ...form, video_url: e.target.value })} className={inputCls} placeholder="https://www.youtube.com/watch?v=…" />
                    </label>
                    <label className="block text-sm sm:col-span-2">
                      {t("learning.admin.fields.body")}
                      <textarea rows={12} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} className={`${inputCls} font-mono`} />
                      <span className="text-xs text-slate-400">{t("learning.admin.fields.bodyHint")}</span>
                    </label>
                    <label className="block text-sm">
                      {t("learning.admin.fields.validity")}
                      <input type="number" min={1} max={120} value={form.validity_months} onChange={(e) => setForm({ ...form, validity_months: e.target.value })} className={inputCls} />
                      <span className="text-xs text-slate-400">{t("learning.admin.fields.validityHint")}</span>
                    </label>
                    <label className="block text-sm">
                      {t("learning.admin.fields.random")}
                      <input type="number" min={1} max={100} value={form.random_count} onChange={(e) => setForm({ ...form, random_count: e.target.value })} className={inputCls} />
                      <span className="text-xs text-slate-400">{t("learning.admin.fields.randomHint")}</span>
                    </label>
                    <label className="block text-sm">
                      {t("learning.admin.fields.dueDays")}
                      <input type="number" min={1} max={365} value={form.default_due_days} onChange={(e) => setForm({ ...form, default_due_days: e.target.value })} className={inputCls} />
                      <span className="text-xs text-slate-400">{t("learning.admin.fields.dueDaysHint")}</span>
                    </label>
                    <div className="space-y-2 text-sm">
                      <label className="flex items-center gap-2">
                        <input type="checkbox" checked={form.external_ok} onChange={(e) => setForm({ ...form, external_ok: e.target.checked })} />
                        {t("learning.admin.fields.externalOk")}
                      </label>
                      <label className="flex items-center gap-2">
                        <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
                        {t("learning.admin.fields.active")}
                      </label>
                    </div>
                    <div className="text-sm sm:col-span-2">
                      {t("learning.admin.fields.autoRoles")}
                      <div className="mt-1 flex flex-wrap gap-2">
                        {roles.map((r) => (
                          <label key={r.key} className="flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1 text-xs">
                            <input
                              type="checkbox"
                              checked={form.auto_roles.includes(r.key)}
                              onChange={(e) =>
                                setForm({
                                  ...form,
                                  auto_roles: e.target.checked ? [...form.auto_roles, r.key] : form.auto_roles.filter((k) => k !== r.key),
                                })
                              }
                            />
                            {r.name}
                          </label>
                        ))}
                      </div>
                      <span className="text-xs text-slate-400">{t("learning.admin.fields.autoRolesHint")}</span>
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <button type="submit" disabled={busy} className={btnP}>
                      {t("learning.admin.save")}
                    </button>
                    {current && perms.can("delete") && (
                      <button type="button" onClick={deleteMaterial} className="rounded-lg border border-rose-300 px-3 py-1.5 text-sm text-rose-700 hover:bg-rose-50">
                        {t("learning.admin.delete")}
                      </button>
                    )}
                    {current?.created_by_name && (
                      <span className="text-xs text-slate-400">
                        {current.created_by_name} · {fd(current.updated_at)}
                      </span>
                    )}
                  </div>
                </form>
              )}

              {tab === "questions" && current && (
                <div className="space-y-3">
                  <p className="text-xs text-slate-500">{t("learning.admin.questions.hint")}</p>
                  {questions.length === 0 && <p className="text-sm text-slate-500">{t("learning.admin.questions.none")}</p>}
                  {questions.map((q, qi) => (
                    <div key={qi} className="rounded-xl border border-slate-200 p-3">
                      <div className="flex items-start gap-2">
                        <span className="mt-2 text-sm text-slate-400">{qi + 1}.</span>
                        <input
                          value={q.text}
                          placeholder={t("learning.admin.questions.text")}
                          onChange={(e) => setQuestions(questions.map((x, i) => (i === qi ? { ...x, text: e.target.value } : x)))}
                          className={`${inputCls} mt-0 flex-1`}
                        />
                        <button type="button" onClick={() => setQuestions(questions.filter((_, i) => i !== qi))} className="mt-1 text-slate-400 hover:text-rose-600" title={t("learning.admin.questions.remove")}>
                          ✕
                        </button>
                      </div>
                      <div className="mt-2 space-y-1 pl-6">
                        {q.options.map((o, oi) => (
                          <div key={oi} className="flex items-center gap-2">
                            <input
                              type="checkbox"
                              checked={o.correct}
                              title={t("learning.admin.questions.correct")}
                              onChange={(e) =>
                                setQuestions(questions.map((x, i) => (i === qi ? { ...x, options: x.options.map((y, j) => (j === oi ? { ...y, correct: e.target.checked } : y)) } : x)))
                              }
                            />
                            <input
                              value={o.text}
                              placeholder={t("learning.admin.questions.option")}
                              onChange={(e) =>
                                setQuestions(questions.map((x, i) => (i === qi ? { ...x, options: x.options.map((y, j) => (j === oi ? { ...y, text: e.target.value } : y)) } : x)))
                              }
                              className={`${inputCls} mt-0 flex-1 ${o.correct ? "border-emerald-400 bg-emerald-50" : ""}`}
                            />
                            <button
                              type="button"
                              onClick={() => setQuestions(questions.map((x, i) => (i === qi ? { ...x, options: x.options.filter((_, j) => j !== oi) } : x)))}
                              className="text-slate-400 hover:text-rose-600"
                            >
                              ✕
                            </button>
                          </div>
                        ))}
                        <button
                          type="button"
                          onClick={() => setQuestions(questions.map((x, i) => (i === qi ? { ...x, options: [...x.options, { text: "", correct: false }] } : x)))}
                          className="text-xs text-sky-700 hover:underline"
                        >
                          + {t("learning.admin.questions.addOption")}
                        </button>
                      </div>
                    </div>
                  ))}
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => setQuestions([...questions, { text: "", options: [{ text: "", correct: true }, { text: "", correct: false }] }])}
                      className={btnG}
                    >
                      + {t("learning.admin.questions.add")}
                    </button>
                    <button type="button" disabled={busy} onClick={saveQuestions} className={btnP}>
                      {t("learning.admin.questions.save")}
                    </button>
                  </div>
                </div>
              )}

              {tab === "files" && current && (
                <div className="space-y-3">
                  <p className="text-xs text-slate-500">{t("learning.admin.files.hint")}</p>
                  <input
                    type="file"
                    accept="application/pdf,image/png,image/jpeg"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) uploadFile(f);
                      e.target.value = "";
                    }}
                    className="block text-sm"
                  />
                  {current.attachments.length === 0 && <p className="text-sm text-slate-500">{t("learning.admin.files.none")}</p>}
                  <ul className="space-y-1 text-sm">
                    {current.attachments.map((att) => (
                      <li key={att.id} className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2">
                        <a href={`/api/learning/materials/${current.id}/attachments/${att.id}`} target="_blank" rel="noopener noreferrer" className="text-sky-700 underline">
                          {att.mime === "application/pdf" ? "📄" : "🖼️"} {att.filename} <span className="text-xs text-slate-400">({fmtSize(att.size)})</span>
                        </a>
                        <button type="button" onClick={() => deleteFile(att.id)} className="text-slate-400 hover:text-rose-600">
                          ✕
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {tab === "assign" && current && (
                <div className="space-y-4">
                  <p className="text-xs text-slate-500">{t("learning.admin.assign.hint")}</p>
                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <div className="mb-1 text-sm font-medium">{t("learning.admin.assign.employees")}</div>
                      <input value={empFilter} onChange={(e) => setEmpFilter(e.target.value)} placeholder="🔍" className={`${inputCls} mb-2`} />
                      <div className="max-h-72 space-y-1 overflow-y-auto rounded-lg border border-slate-200 p-2">
                        {filteredEmp.map((e) => (
                          <label key={e.id} className="flex items-center gap-2 text-sm">
                            <input
                              type="checkbox"
                              checked={assignEmp.includes(e.id)}
                              onChange={(ev) => setAssignEmp(ev.target.checked ? [...assignEmp, e.id] : assignEmp.filter((x) => x !== e.id))}
                            />
                            {e.last_name} {e.first_name}
                            {e.is_contractor && <span className="text-xs text-amber-700">({t("learning.admin.assign.contractor")})</span>}
                          </label>
                        ))}
                      </div>
                    </div>
                    <div className="space-y-3">
                      <div>
                        <div className="mb-1 text-sm font-medium">{t("learning.admin.assign.roles")}</div>
                        <div className="flex flex-wrap gap-2">
                          {roles.map((r) => (
                            <label key={r.key} className="flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1 text-xs">
                              <input
                                type="checkbox"
                                checked={assignRoles.includes(r.key)}
                                onChange={(e) => setAssignRoles(e.target.checked ? [...assignRoles, r.key] : assignRoles.filter((k) => k !== r.key))}
                              />
                              {r.name}
                            </label>
                          ))}
                        </div>
                        {current.auto_roles.length > 0 && (
                          <p className="mt-1 text-xs text-slate-400">{t("learning.admin.assign.autoRolesNow", { roles: current.auto_roles.join(", ") })}</p>
                        )}
                      </div>
                      <label className="block text-sm">
                        {t("learning.admin.assign.due")}
                        <input type="date" value={assignDue} onChange={(e) => setAssignDue(e.target.value)} className={inputCls} />
                      </label>
                      <button type="button" disabled={busy || (assignEmp.length === 0 && assignRoles.length === 0)} onClick={assign} className={btnP}>
                        {t("learning.admin.assign.submit")}
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {tab === "results" && current && (
                <div className="space-y-2">
                  {results.length === 0 && <p className="text-sm text-slate-500">{t("learning.admin.results.none")}</p>}
                  {results.length > 0 && (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead className="text-left text-xs uppercase text-slate-500">
                          <tr>
                            <th className="py-1 pr-2">{t("learning.admin.results.employee")}</th>
                            <th className="py-1 pr-2">{t("learning.admin.results.status")}</th>
                            <th className="py-1 pr-2">{t("learning.admin.results.due")}</th>
                            <th className="py-1 pr-2">{t("learning.admin.results.completed")}</th>
                            <th className="py-1 pr-2">{t("learning.admin.results.valid")}</th>
                            <th className="py-1 pr-2">{t("learning.admin.results.attempts")}</th>
                            <th className="py-1 pr-2">{t("learning.admin.results.best")}</th>
                            <th className="py-1 pr-2">{t("learning.admin.results.source")}</th>
                            <th />
                          </tr>
                        </thead>
                        <tbody>
                          {results.map((r) => (
                            <tr key={r.assignment_id} className="border-t border-slate-100">
                              <td className="py-1 pr-2">
                                {r.employee_name}
                                {r.is_contractor && <span className="ml-1 text-xs text-amber-700">({t("learning.admin.assign.contractor")})</span>}
                              </td>
                              <td className="py-1 pr-2">
                                <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_CLS[r.status]}`}>{t(`learning.status.${r.status}`)}</span>
                              </td>
                              <td className="py-1 pr-2">{fd(r.due_date)}</td>
                              <td className="py-1 pr-2">{fd(r.completed_at)}</td>
                              <td className="py-1 pr-2">{fd(r.valid_until)}</td>
                              <td className="py-1 pr-2">{r.attempts}</td>
                              <td className="py-1 pr-2">{r.best_score != null ? `${r.best_score} %` : "—"}</td>
                              <td className="py-1 pr-2 text-xs text-slate-500">{t(`learning.source.${r.source}`)}</td>
                              <td className="py-1 text-right">
                                <button type="button" onClick={() => unassign(r)} className="text-xs text-slate-400 hover:text-rose-600">
                                  {t("learning.admin.results.unassign")}
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
